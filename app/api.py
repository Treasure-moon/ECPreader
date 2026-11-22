"""HTTP API：文档、翻译任务、设置、凭据、历史、词典、导出。"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import config, db, exporting, security
from .config import AI_PRESETS, FREE_PROVIDERS
from .parsers import ParseError, parse_document
from .translate import dictionary, pipeline
from .translate.errors import TranslationError

router = APIRouter(prefix="/api")

MAGIC = {".pdf": b"%PDF", ".docx": b"PK\x03\x04"}


# ------------------------------------------------------------------ helpers
def _pages():
    """PyMuPDF 很重，延后到真正需要渲染/取词时再导入，缩短启动时间。"""
    from . import pages

    return pages


def _call_pages(name: str, *args):
    """在 to_thread 里调用，连同 PyMuPDF 的首次导入也放到工作线程。"""
    return getattr(_pages(), name)(*args)


def _decode_filename(name: str) -> str:
    if not name:
        return "document"
    if any("\u4e00" <= ch <= "\u9fff" for ch in name):
        return name
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _http_error(exc: TranslationError) -> HTTPException:
    return HTTPException(status_code=400, detail=exc.to_dict())


def _safe_name(name: str, extension: str) -> str:
    base = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip() or "document"
    return f"{base[:80]}{extension}"


def _with_layout(document: dict | None) -> dict | None:
    if not document:
        return document
    meta = document.get("meta") or {}
    document["layout"] = "abs" if meta.get("layout") == "abs" and meta.get("pages_meta") else "flow"
    document["pages_meta"] = meta.get("pages_meta") or []
    return document


def _engine_info(settings: dict) -> dict:
    mode = str(settings.get("engine_mode") or "free")
    if mode == "ai":
        provider = str(settings.get("ai_provider") or "deepseek")
        preset = AI_PRESETS.get(provider, AI_PRESETS["custom"])
        try:
            cfg = pipeline.ai_config(settings)
            ready = bool(cfg["api_key"]) or any(
                h in cfg["base_url"] for h in ("127.0.0.1", "localhost")
            )
            return {
                "mode": "ai",
                "label": f"AI 翻译 · {preset.get('label', provider)}",
                "detail": cfg["model"],
                "ready": ready,
                "provider": provider,
            }
        except TranslationError as exc:
            return {"mode": "ai", "label": "AI 翻译", "detail": "未配置", "ready": False, "error": exc.to_dict()}
    provider = str(settings.get("free_provider") or "auto")
    return {
        "mode": "free",
        "label": "传统机翻（免密钥）",
        "detail": FREE_PROVIDERS.get(provider, {}).get("label", provider),
        "ready": True,
        "provider": provider,
    }


def _masked_keys() -> dict:
    try:
        keys = security.VAULT.get("api_keys") or {}
    except security.SecurityError:
        keys = {}
    return {
        provider: {"configured": bool(key), "masked": security.mask_secret(key)}
        for provider, key in (keys or {}).items()
    }


# ------------------------------------------------------------------- health
@router.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "time": db.now(),
    }


# ---------------------------------------------------------------- documents
@router.post("/documents")
async def upload_document(file: UploadFile = File(...)) -> dict:
    filename = _decode_filename(file.filename or "document")
    suffix = Path(filename).suffix.lower()
    if suffix not in MAGIC:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "parse_error",
                "message": f"暂不支持 {suffix or '该'} 格式",
                "hint": "目前支持 PDF 与 Word .docx。旧版 .doc 请先用 Word 另存为 .docx。",
            },
        )
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "file_too_large",
                "message": f"文件超过 {config.MAX_UPLOAD_MB}MB",
                "hint": "请压缩或拆分后再试；超大的扫描版 PDF 建议先做 OCR。",
            },
        )
    if not data.startswith(MAGIC[suffix]):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "parse_error",
                "message": "文件内容与扩展名不符",
                "hint": "文件可能已损坏或被重命名。请用原始文件重新导出后再试。",
            },
        )

    config.ensure_dirs()
    stored = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(data)
    try:
        result = await asyncio.to_thread(parse_document, stored, filename)
    except ParseError as exc:
        stored.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail={"code": "parse_error", "message": str(exc), "hint": "请检查文件后重试。"},
        ) from exc
    except Exception as exc:
        stored.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "parse_error",
                "message": "解析文档时出错",
                "hint": "文件可能包含损坏的对象或加密保护。可尝试另存后重试。",
                "detail": str(exc)[:300],
            },
        ) from exc

    meta = dict(result.get("meta") or {})
    meta["layout"] = result.get("layout") or "flow"
    meta["stored_file"] = str(stored)
    meta["original_filename"] = filename
    doc_id = db.create_document(
        title=result["title"],
        filename=filename,
        filetype=suffix.lstrip("."),
        page_count=result.get("page_count"),
        blocks=result["blocks"],
        toc=result["toc"],
        meta=meta,
    )
    document = db.get_document(doc_id)
    return {"document": _with_layout(document)}


@router.get("/documents")
def list_documents(query: str = "", limit: int = 50) -> dict:
    documents = db.list_documents(limit=min(max(limit, 1), 200), query=query)
    return {"documents": documents}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str) -> dict:
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在或已被删除")
    return {"document": _with_layout(document)}


@router.get("/documents/{doc_id}/pages/{page_no}.png")
async def document_page(doc_id: str, page_no: int, dpi: int = 160) -> FileResponse:
    meta = db.get_document_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="文档不存在")
    stored = (meta.get("meta") or {}).get("stored_file")
    if not stored or not Path(stored).exists():
        raise HTTPException(status_code=404, detail="原始文件已丢失，请重新导入")
    try:
        path = await asyncio.to_thread(
            _call_pages, "render_page_png", Path(stored), doc_id, page_no, dpi
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")


@router.get("/documents/{doc_id}/pages/{page_no}/words")
async def document_page_words(doc_id: str, page_no: int) -> dict:
    """页面单词坐标（含所属句号），供原版视图做可选中文本层。"""
    meta = db.get_document_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="文档不存在")
    stored = (meta.get("meta") or {}).get("stored_file")
    if not stored or not Path(stored).exists():
        raise HTTPException(status_code=404, detail="原始文件已丢失，请重新导入")
    document = db.get_document(doc_id)
    try:
        return await asyncio.to_thread(
            _call_pages, "page_words", Path(stored), page_no, document.get("blocks") or []
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/documents/{doc_id}/reparse")
async def reparse_document(doc_id: str) -> dict:
    """重新解析（旧版本导入的文档没有坐标，重新解析后即可用原版视图）。"""
    document = db.get_document_meta(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    meta = document.get("meta") or {}
    stored = meta.get("stored_file")
    if not stored or not Path(stored).exists():
        raise HTTPException(status_code=404, detail="原始文件已丢失，请重新导入")
    try:
        result = await asyncio.to_thread(parse_document, Path(stored), document["filename"])
    except ParseError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "parse_error", "message": str(exc), "hint": "请检查文件后重试。"},
        ) from exc
    new_meta = dict(result.get("meta") or {})
    new_meta["layout"] = result.get("layout") or "flow"
    new_meta["stored_file"] = stored
    new_meta["original_filename"] = meta.get("original_filename") or document["filename"]
    kept = await asyncio.to_thread(
        db.replace_blocks,
        doc_id,
        blocks=result["blocks"],
        toc=result["toc"],
        page_count=result.get("page_count"),
        meta=new_meta,
        title=result["title"],
    )
    _pages().clear_page_cache(doc_id)
    updated = db.get_document(doc_id)
    return {"document": _with_layout(updated), "kept": kept}


@router.post("/documents/{doc_id}/word-at")
async def word_at(doc_id: str, payload: dict = Body(default={})) -> dict:
    meta = db.get_document_meta(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="文档不存在")
    stored = (meta.get("meta") or {}).get("stored_file")
    if not stored or not Path(stored).exists():
        raise HTTPException(status_code=404, detail="原始文件已丢失，请重新导入")
    try:
        page_no = int(payload.get("page") or 0)
        x = float(payload.get("x") or 0)
        y = float(payload.get("y") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="坐标参数不正确") from exc
    hit = await asyncio.to_thread(_call_pages, "word_at", Path(stored), page_no, x, y)
    if not hit:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "empty_result",
                "message": "这里没有可识别的英文单词",
                "hint": "请把鼠标对准单词再双击；扫描版 PDF 需要先做 OCR。",
            },
        )
    document = db.get_document(doc_id)
    block = _pages().block_at((document or {}).get("blocks") or [], page_no, x, y)
    paragraph = (block or {}).get("text") or ""
    sentence = _pages().sentence_around(paragraph, hit["word"]) if paragraph else hit["word"]
    try:
        result = await dictionary.lookup(hit["word"], sentence, paragraph)
    except TranslationError as exc:
        raise _http_error(exc) from exc
    return result


@router.delete("/documents")
def delete_all_documents() -> dict:
    documents = db.list_documents(limit=10000)
    stored_files = []
    for item in documents:
        meta = db.get_document_meta(item["id"]) or {}
        path = (meta.get("meta") or {}).get("stored_file")
        if path:
            stored_files.append(path)
    db.delete_all_documents()
    for path in stored_files:
        Path(path).unlink(missing_ok=True)
        doc_id = Path(path).stem
        _pages().clear_page_cache(doc_id)
    return {"ok": True, "deleted": len(documents)}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str) -> dict:
    document = db.get_document_meta(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    stored = (document.get("meta") or {}).get("stored_file")
    db.delete_document(doc_id)
    if stored:
        Path(stored).unlink(missing_ok=True)
    _pages().clear_page_cache(doc_id)
    return {"ok": True}


# ----------------------------------------------------------------- translate
@router.post("/documents/{doc_id}/translate")
async def translate_document(doc_id: str, payload: dict = Body(default={})) -> dict:
    mode = payload.get("mode")
    settings = db.get_settings()
    target_mode = mode if mode in ("free", "ai") else str(settings.get("engine_mode") or "free")
    if target_mode == "ai":
        try:
            pipeline.build_ai(settings)
        except TranslationError as exc:
            raise _http_error(exc) from exc
    try:
        job = await pipeline.start_translation(
            doc_id,
            mode=target_mode,
            seqs=payload.get("seqs"),
            force=bool(payload.get("force")),
        )
    except TranslationError as exc:
        raise _http_error(exc) from exc
    return {"job": job.snapshot(0)}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, cursor: int = 0) -> dict:
    job = pipeline.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="翻译任务不存在或已过期")
    return {"job": job.snapshot(max(0, cursor))}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    return {"ok": pipeline.cancel_job(job_id)}


@router.post("/documents/{doc_id}/blocks/{seq}/translate")
async def translate_block(doc_id: str, seq: int, payload: dict = Body(default={})) -> dict:
    try:
        translation = await pipeline.retranslate_block(doc_id, seq, payload.get("mode"))
    except TranslationError as exc:
        raise _http_error(exc) from exc
    return {"seq": seq, "translation": translation}


# ------------------------------------------------------------------ exports
@router.get("/documents/{doc_id}/export")
def export_document(doc_id: str, format: str = "md") -> StreamingResponse:
    document = db.get_document(doc_id)
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")
    fmt = format.lower()
    if fmt == "md":
        content, media, extension = exporting.to_markdown(document), "text/markdown; charset=utf-8", ".md"
    elif fmt == "html":
        content, media, extension = exporting.to_html(document), "text/html; charset=utf-8", ".html"
    elif fmt == "txt":
        content, media, extension = exporting.to_plain(document), "text/plain; charset=utf-8", ".txt"
    elif fmt == "json":
        content = json.dumps(document, ensure_ascii=False, indent=2)
        media, extension = "application/json; charset=utf-8", ".json"
    else:
        raise HTTPException(status_code=400, detail="不支持的导出格式")
    name = _safe_name(document.get("title") or "document", extension)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(io.BytesIO(content.encode("utf-8")), media_type=media, headers=headers)


# ----------------------------------------------------------------- settings
@router.get("/settings")
def read_settings() -> dict:
    settings = db.get_settings()
    return {
        "settings": settings,
        "presets": AI_PRESETS,
        "free_providers": FREE_PROVIDERS,
        "security": {
            **security.VAULT.status(),
            "masked_keys": _masked_keys(),
            "data_dir": str(config.DATA_DIR),
            "db_file": str(config.DB_FILE),
            "data_dir_fallback": config.DATA_DIR_FALLBACK,
        },
        "engine": _engine_info(settings),
    }


SETTING_VALIDATORS = {
    "engine_mode": lambda v: v in ("free", "ai"),
    "free_provider": lambda v: v in FREE_PROVIDERS,
    "ai_provider": lambda v: v in AI_PRESETS,
    "ai_temperature": lambda v: isinstance(v, (int, float)) and 0 <= float(v) <= 1,
    "ai_concurrency": lambda v: isinstance(v, int) and 1 <= v <= 6,
    "font_size": lambda v: isinstance(v, int) and 12 <= v <= 26,
    "theme": lambda v: v in ("light", "sepia", "dark"),
    "translate_references": lambda v: isinstance(v, bool),
    "translate_formulas": lambda v: isinstance(v, bool),
    "show_page_badges": lambda v: isinstance(v, bool),
    "free_email": lambda v: isinstance(v, str) and len(v) <= 200,
    "glossary": lambda v: isinstance(v, str) and len(v) <= 8000,
    "ai_base_url": lambda v: isinstance(v, str) and len(v) <= 500,
    "ai_model": lambda v: isinstance(v, str) and len(v) <= 200,
}


@router.put("/settings")
def update_settings(payload: dict = Body(default={})) -> dict:
    values = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    clean: dict = {}
    for key, value in values.items():
        validator = SETTING_VALIDATORS.get(key)
        if validator is None:
            continue
        if validator(value):
            clean[key] = value
    if clean:
        db.set_settings(clean)
    return read_settings()


@router.post("/settings/credentials")
def save_credentials(payload: dict = Body(default={})) -> dict:
    provider = str(payload.get("provider") or "")
    if provider not in AI_PRESETS:
        raise HTTPException(status_code=400, detail="未知的 AI 服务商")
    key = str(payload.get("api_key") or "").strip()
    clear = bool(payload.get("clear"))
    if not clear and not key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")
    if len(key) > 400:
        raise HTTPException(status_code=400, detail="API Key 过长，请检查是否复制了多余内容")
    try:
        keys = security.VAULT.get("api_keys") or {}
        if clear:
            keys.pop(provider, None)
        else:
            keys[provider] = key
        security.VAULT.set_many({"api_keys": keys})
    except security.SecurityError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "vault_error", "message": "凭据加密保存失败", "hint": str(exc)},
        ) from exc
    return {"ok": True, "provider": provider, "configured": not clear, "masked": security.mask_secret(keys.get(provider))}


@router.post("/settings/test")
async def test_engine(payload: dict = Body(default={})) -> dict:
    settings = db.get_settings()
    mode = payload.get("mode") or settings.get("engine_mode") or "free"
    started = time.time()
    if mode == "ai":
        try:
            translator = pipeline.build_ai(settings)
        except TranslationError as exc:
            return {"ok": False, "error": exc.to_dict()}
        try:
            sample = await translator.test_connection()
            return {
                "ok": True,
                "engine": _engine_info(settings)["label"],
                "sample": sample,
                "latency_ms": int((time.time() - started) * 1000),
            }
        except TranslationError as exc:
            return {"ok": False, "error": exc.to_dict()}
        except Exception as exc:
            return {
                "ok": False,
                "error": {"code": "unknown", "message": "测试失败", "hint": str(exc)[:200]},
            }
        finally:
            await translator.aclose()

    translator = pipeline.build_free(settings)
    try:
        sample_list = await translator.translate(
            ["This paper proposes a new method for document layout analysis."]
        )
        provider = await translator.resolved_provider()
        labels = {"google": "Google 翻译", "bing": "Bing 翻译", "mymemory": "MyMemory"}
        return {
            "ok": True,
            "engine": "传统机翻 · " + labels.get(provider, provider),
            "sample": sample_list[0] if sample_list else "",
            "latency_ms": int((time.time() - started) * 1000),
            "provider": provider,
        }
    except TranslationError as exc:
        return {"ok": False, "error": exc.to_dict()}
    except Exception as exc:
        return {"ok": False, "error": {"code": "unknown", "message": "测试失败", "hint": str(exc)[:200]}}
    finally:
        await translator.aclose()


# ------------------------------------------------------------------ history
@router.get("/history")
def history(query: str = "", limit: int = 100) -> dict:
    return {"history": db.list_history(limit=min(max(limit, 1), 300), query=query)}


@router.delete("/history")
def clear_history() -> dict:
    db.clear_history()
    return {"ok": True}


@router.get("/history/export")
def export_backup() -> StreamingResponse:
    backup = db.export_all()
    backup["secrets_included"] = False
    backup["note"] = (
        "此备份包含文档、段落译文、翻译历史与设置，不包含任何 API Key。"
        "凭据以 Windows DPAPI 加密保存在 vault.bin 中，只能由同一 Windows 账户解密。"
    )
    content = json.dumps(backup, ensure_ascii=False, indent=2)
    name = _safe_name(f"ecpreader-backup-{db.now()[:10]}", ".json")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type="application/json; charset=utf-8",
        headers=headers,
    )


# ------------------------------------------------------------------- system
@router.get("/system/info")
def system_info() -> dict:
    size = config.DB_FILE.stat().st_size if config.DB_FILE.exists() else 0
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "data_dir": str(config.DATA_DIR),
        "db_file": str(config.DB_FILE),
        "db_size": size,
        "protection": security.VAULT.status()["protection"],
    }


EXTERNAL_LINKS = {
    "https://github.com/Treasure-moon/ECPreader",
    "https://github.com/Treasure-moon/ECPreader/releases/latest",
    "mailto:treasure_moon@163.com",
}


@router.post("/system/open-external")
def open_external(payload: dict = Body(default={})) -> dict:
    """只允许打开项目仓库、更新页和作者邮箱，避免被页面用作任意 URL 跳板。"""
    url = str(payload.get("url") or "").strip()
    if url not in EXTERNAL_LINKS:
        raise HTTPException(status_code=400, detail="不支持的外部链接")
    try:
        import webbrowser

        opened = webbrowser.open(url)
        return {"ok": bool(opened)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法打开链接：{exc}") from exc


@router.post("/system/reveal")
def reveal(payload: dict = Body(default={})) -> dict:
    target = Path(str(payload.get("target") or config.DATA_DIR)).resolve()
    try:
        target.relative_to(config.DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="只能打开应用数据目录")
    if not target.exists():
        raise HTTPException(status_code=404, detail="目录不存在")
    try:
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(target)])
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法打开目录：{exc}") from exc


# --------------------------------------------------------------- data dir
def _tree_size(root: Path) -> int:
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _pick_folder_blocking(initial: str) -> str:
    if os.name != "nt":
        return ""
    script = """Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择 ECPreader 数据目录'
$dialog.SelectedPath = '__INITIAL__'
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::WriteAllText('__OUT__', $dialog.SelectedPath, [System.Text.Encoding]::UTF8)
}
"""
    with tempfile.TemporaryDirectory(prefix="ecp-pick-") as folder:
        out_file = Path(folder) / "picked.txt"
        script_file = Path(folder) / "pick.ps1"
        script = script.replace("__INITIAL__", initial.replace("'", "''"))
        script = script.replace("__OUT__", str(out_file).replace("'", "''"))
        script_file.write_text(script, encoding="utf-8-sig")
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(script_file)],
                capture_output=True,
                timeout=300,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        try:
            return out_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""


@router.post("/system/pick-folder")
async def pick_folder(payload: dict = Body(default={})) -> dict:
    initial = str(payload.get("initial") or config.DATA_DIR)
    path = await asyncio.to_thread(_pick_folder_blocking, initial)
    return {"path": path}


@router.get("/system/storage")
def storage_info() -> dict:
    data_dir = config.DATA_DIR
    writable = False
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError:
        pass
    return {
        "data_dir": str(data_dir),
        "db_file": str(config.DB_FILE),
        "db_size": config.DB_FILE.stat().st_size if config.DB_FILE.exists() else 0,
        "total_size": _tree_size(data_dir),
        "config_file": str(config.USER_CONFIG_FILE),
        "writable": writable,
    }


@router.post("/system/data-dir")
def change_data_dir(payload: dict = Body(default={})) -> dict:
    raw = str(payload.get("path") or "").strip().strip('"')
    migrate = bool(payload.get("migrate"))
    if not raw:
        raise HTTPException(status_code=400, detail="请先选择新的数据目录")
    target = Path(raw).expanduser()
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail="请填写完整路径（例如 D:/ECPreaderData）")
    current = config.DATA_DIR.resolve()
    try:
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"路径无效：{exc}") from exc
    if target == current:
        raise HTTPException(status_code=400, detail="新目录与当前数据目录相同")
    if target.is_relative_to(current) or current.is_relative_to(target):
        raise HTTPException(status_code=400, detail="新目录不能放在当前数据目录内部，也不能包含当前数据目录")
    if target.exists() and not target.is_dir():
        raise HTTPException(status_code=400, detail="该路径是一个文件，请选择文件夹")
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"目录不可写：{exc}") from exc

    copied: list[str] = []
    if migrate:
        db.checkpoint()
        for name in ("app.db", "app.db-wal", "app.db-shm", "vault.bin", "vault.key", "app.log"):
            source = current / name
            if source.is_file():
                shutil.copy2(source, target / name)
                copied.append(name)
        for name in ("uploads", "exports", "pages"):
            source = current / name
            if source.is_dir():
                shutil.copytree(source, target / name, dirs_exist_ok=True)
                copied.append(name)
    config.write_user_config({"data_dir": str(target)})
    return {
        "ok": True,
        "data_dir": str(target),
        "previous": str(current),
        "migrated": bool(copied) if migrate else False,
        "restart_required": True,
    }


def _relaunch_app() -> None:
    time.sleep(1.0)
    try:
        if getattr(sys, "frozen", False):
            command = [sys.executable, *sys.argv[1:]]
        else:
            command = [sys.executable, "-m", "app.launcher", *sys.argv[1:]]
        env = {**os.environ, "ECP_RESTARTED": "1"}
        subprocess.Popen(
            command,
            cwd=str(config.BASE_DIR),
            close_fds=True,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except Exception as exc:  # pragma: no cover - 受系统环境影响
        print(f"[restart] 无法启动新实例：{exc}")
        return
    time.sleep(0.4)
    os._exit(0)


@router.post("/system/restart")
def restart_app(background: BackgroundTasks) -> dict:
    background.add_task(_relaunch_app)
    return {"ok": True, "message": "正在重启 ECPreader…"}


# ---------------------------------------------------------------- dictionary
@router.post("/dict/lookup")
async def dict_lookup(payload: dict = Body(default={})) -> dict:
    word = str(payload.get("word") or "")
    sentence = str(payload.get("sentence") or "")
    paragraph = str(payload.get("paragraph") or "")
    try:
        return await dictionary.lookup(word, sentence, paragraph)
    except TranslationError as exc:
        raise _http_error(exc) from exc