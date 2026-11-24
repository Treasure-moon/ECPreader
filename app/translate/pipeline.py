"""翻译流水线：分段、上下文、并发、进度、错误归类、单段重译。"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .. import db, security
from ..config import AI_PRESETS
from .ai import AITranslator
from .errors import TranslationError, error_payload
from .free import FreeTranslator, needs_translation
from .mathmask import mask_math, restore_math

SKIP_TYPES = {"formula", "header"}

# 这些错误继续请求也没有意义：额度/凭据/配置问题必须立刻停下并提示用户。
FATAL_CODES = {
    "free_quota",
    "quota",
    "unauthorized",
    "config_missing",
}
MAX_CONSECUTIVE_FAILURES = 3


def build_free(settings: dict[str, Any] | None = None) -> FreeTranslator:
    settings = settings or db.get_settings()
    return FreeTranslator(
        provider=str(settings.get("free_provider") or "auto"),
        email=str(settings.get("free_email") or ""),
        cached_provider=str(settings.get("free_cached_provider") or ""),
        concurrency=3,
    )


def ai_config(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or db.get_settings()
    provider = str(settings.get("ai_provider") or "deepseek")
    preset = AI_PRESETS.get(provider) or AI_PRESETS["custom"]
    base_url = str(settings.get("ai_base_url") or preset.get("base_url") or "")
    model = str(settings.get("ai_model") or preset.get("model") or "")
    try:
        keys = security.VAULT.get("api_keys") or {}
        api_key = str((keys or {}).get(provider, ""))
    except security.SecurityError as exc:
        raise TranslationError(
            "config_missing",
            message="无法读取加密凭据仓库",
            hint=str(exc),
        ) from exc
    return {
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "kind": str(preset.get("kind") or "openai"),
        "temperature": float(settings.get("ai_temperature") or 0.2),
        "timeout": 180.0 if str(preset.get("kind")) == "ollama" else 120.0,
    }


def build_ai(settings: dict[str, Any] | None = None) -> AITranslator:
    config = ai_config(settings)
    if not config["base_url"] or not config["model"]:
        raise TranslationError("config_missing")
    local = any(h in config["base_url"] for h in ("127.0.0.1", "localhost", "0.0.0.0"))
    if not local and not config["api_key"]:
        raise TranslationError(
            "config_missing",
            message="尚未填写 API Key",
            hint="打开「翻译设置 → AI 引擎」，填入服务商提供的 API Key（只会加密保存在本机）。",
        )
    return AITranslator(
        provider=config["provider"],
        base_url=config["base_url"],
        model=config["model"],
        api_key=config["api_key"],
        kind=config["kind"],
        temperature=config["temperature"],
        timeout=config["timeout"],
    )


def engine_label(mode: str, settings: dict[str, Any]) -> tuple[str, str]:
    if mode == "ai":
        provider = str(settings.get("ai_provider") or "deepseek")
        model = str(settings.get("ai_model") or "")
        return f"ai:{provider}", model
    provider = str(settings.get("free_provider") or "auto")
    return f"free:{provider}", ""


# ------------------------------------------------------------------- helpers
def block_segments(block: dict[str, Any]) -> list[dict[str, str]]:
    if block.get("type") == "table":
        return [cell for row in block.get("cells") or [] for cell in row]
    return list(block.get("segments") or [])


def block_source_text(block: dict[str, Any]) -> str:
    if block.get("type") == "table":
        return " | ".join(
            cell["src"] for row in block.get("cells") or [] for cell in row
        )
    return block.get("text") or ""


def block_kind(block: dict[str, Any]) -> str:
    block_type = block.get("type", "paragraph")
    if block_type in ("heading", "title"):
        return "heading"
    if block_type == "caption":
        return "caption"
    if block_type == "table":
        return "cell"
    if block_type == "list":
        return "list"
    return "paragraph"


def translation_text(block: dict[str, Any]) -> str:
    translation = block.get("translation") or {}
    segments = translation.get("segments") or {}
    if not segments:
        return ""
    if block.get("type") == "table":
        parts = []
        for row in block.get("cells") or []:
            parts.extend(segments.get(cell["sid"], "") for cell in row)
        return " | ".join(part for part in parts if part)
    return " ".join(segments.get(seg["sid"], "") for seg in block.get("segments") or []).strip()


def is_eligible(
    block: dict[str, Any], settings: dict[str, Any], *, force: bool, selected: set[int] | None
) -> bool:
    seq = int(block.get("seq", -1))
    if selected is not None and seq not in selected:
        return False
    block_type = block.get("type")
    if block_type in SKIP_TYPES and not settings.get("translate_formulas"):
        return False
    if block_type == "reference" and not settings.get("translate_references"):
        return False
    if not block_segments(block):
        return False
    if not force:
        translation = block.get("translation") or {}
        if translation.get("status") == "done" and translation.get("segments"):
            return False
    return bool(block_source_text(block).strip())


def _section_for(blocks: list[dict[str, Any]], seq: int) -> str:
    section = ""
    for block in blocks:
        if block["seq"] > seq:
            break
        if block.get("type") in ("heading", "title"):
            section = block.get("text") or section
    return section


def _context_for(blocks: list[dict[str, Any]], seq: int, glossary_text: str) -> dict[str, Any]:
    prev_en = ""
    prev_zh = ""
    for block in reversed(blocks):
        if block["seq"] >= seq:
            continue
        if block.get("type") == "table":
            continue
        source = block_source_text(block).strip()
        if not source:
            continue
        prev_en = source
        prev_zh = translation_text(block)
        break
    return {
        "section": _section_for(blocks, seq),
        "prev_en": prev_en,
        "prev_zh": prev_zh,
        "glossary": glossary_text,
    }


def _make_translation(
    engine: str, model: str, mapping: dict[str, str]
) -> dict[str, Any]:
    return {
        "status": "done",
        "engine": engine,
        "model": model,
        "segments": mapping,
        "updated_at": db.now(),
    }


# ----------------------------------------------------------------------- jobs
@dataclass
class Job:
    id: str
    doc_id: str
    mode: str
    engine: str
    model: str
    history_id: str
    title: str
    total: int = 0
    done: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    status: str = "running"
    error: dict[str, Any] | None = None
    fatal_error: dict[str, Any] | None = None
    updates: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def snapshot(self, cursor: int = 0) -> dict[str, Any]:
        updates = self.updates[cursor:] if cursor >= 0 else self.updates
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "title": self.title,
            "mode": self.mode,
            "engine": self.engine,
            "model": self.model,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "failed": self.failed,
            "error": self.error,
            "updates": updates,
            "cursor": len(self.updates),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


JOBS: dict[str, Job] = {}
_GLOSSARY_CACHE: dict[str, str] = {}


def _fatal_stop(job: Job, exc: TranslationError) -> None:
    """额度或配置类错误：立即停止整个任务，不再向接口发后续请求。"""
    job.cancelled = True
    job.status = "failed"
    job.error = exc.to_dict()
    job.fatal_error = job.error


def get_job(job_id: str) -> Job | None:
    return JOBS.get(job_id)


def cancel_job(job_id: str) -> bool:
    job = JOBS.get(job_id)
    if job and job.status == "running":
        job.cancelled = True
        return True
    return False


def _prune_jobs() -> None:
    finished = [job for job in JOBS.values() if job.status != "running"]
    if len(finished) > 20:
        finished.sort(key=lambda j: j.finished_at or 0)
        for job in finished[: len(finished) - 20]:
            JOBS.pop(job.id, None)


async def start_translation(
    doc_id: str,
    *,
    mode: str | None = None,
    seqs: list[int] | None = None,
    force: bool = False,
) -> Job:
    document = db.get_document(doc_id)
    if not document:
        raise TranslationError("parse_error", message="文档不存在或已被删除")
    settings = db.get_settings()
    mode = mode or str(settings.get("engine_mode") or "free")
    if mode not in ("free", "ai"):
        mode = "free"
    selected = set(int(s) for s in seqs) if seqs else None
    blocks = document.get("blocks") or []
    targets = [
        block
        for block in blocks
        if is_eligible(block, settings, force=force, selected=selected)
    ]
    if not targets:
        raise TranslationError(
            "empty_result",
            message="没有需要翻译的段落",
            hint="文档可能已经全部翻译完成；如需重新翻译，请使用「重新翻译」。",
        )

    engine, model = engine_label(mode, settings)
    history_id = db.add_history(
        doc_id=doc_id,
        title=document["title"],
        mode=mode,
        engine=engine,
        model=model,
        status="running",
        block_total=len(targets),
        char_count=sum(len(block_source_text(b)) for b in targets),
    )
    job = Job(
        id=uuid.uuid4().hex,
        doc_id=doc_id,
        mode=mode,
        engine=engine,
        model=model,
        history_id=history_id,
        title=document["title"],
        total=len(targets),
    )
    JOBS[job.id] = job
    _prune_jobs()
    db.update_document_status(doc_id, "translating")
    asyncio.create_task(run_job(job, document, settings, targets))
    return job


async def run_job(
    job: Job,
    document: dict[str, Any],
    settings: dict[str, Any],
    targets: list[dict[str, Any]],
) -> None:
    blocks = document.get("blocks") or []
    translator: AITranslator | FreeTranslator | None = None
    engine = job.engine
    model = job.model
    try:
        if job.mode == "ai":
            translator = build_ai(settings)
        else:
            translator = build_free(settings)

        if job.mode == "ai":
            glossary_text = await _glossary_for(document, translator)
            concurrency = max(1, min(6, int(settings.get("ai_concurrency") or 3)))
            await _run_ai(job, document, blocks, targets, translator, glossary_text, concurrency)
        else:
            free = translator
            await _run_free(job, blocks, targets, free)
            if job.fatal_error is None:
                resolved = await free.resolved_provider()
                if resolved:
                    db.set_settings({"free_cached_provider": resolved})
    except TranslationError as exc:
        job.error = exc.to_dict()
        job.status = "failed"
        if isinstance(translator, FreeTranslator):
            await translator.aclose()
        if isinstance(translator, AITranslator):
            await translator.aclose()
        _finalize(job)
        return
    except Exception as exc:  # 兜底：不把异常吞掉
        job.error = error_payload(exc)
        job.status = "failed" if job.done == 0 else "partial"
        if translator is not None:
            try:
                await translator.aclose()
            except Exception:
                pass
        _finalize(job)
        return

    if job.fatal_error is not None:
        job.status = "failed"
        job.error = job.fatal_error
        job.finished_at = time.time()
        if isinstance(translator, FreeTranslator):
            await translator.aclose()
        if isinstance(translator, AITranslator):
            await translator.aclose()
        _finalize(job)
        return

    job.status = "done" if job.failed == 0 else ("partial" if job.done else "failed")
    job.finished_at = time.time()
    if isinstance(translator, FreeTranslator):
        await translator.aclose()
    if isinstance(translator, AITranslator):
        await translator.aclose()
    _finalize(job)


def _finalize(job: Job) -> None:
    if job.finished_at is None:
        job.finished_at = time.time()
    if job.status == "running":
        job.status = "done" if job.failed == 0 else "partial"
    status = "done" if job.status == "done" else job.status
    db.update_document_status(
        job.doc_id,
        "translated" if status == "done" else "partial",
    )
    error_text = ""
    if job.error:
        error_text = f"{job.error.get('code')}: {job.error.get('message')}"
    elif job.failed:
        error_text = f"{job.failed} 个段落翻译失败"
    db.update_history(
        job.history_id,
        status="done" if status == "done" else status,
        block_done=job.done,
        block_total=job.total,
        finished_at=db.now(),
        error=error_text,
    )


async def _glossary_for(
    document: dict[str, Any], translator: AITranslator
) -> str:
    doc_id = document["id"]
    if doc_id in _GLOSSARY_CACHE:
        return _GLOSSARY_CACHE[doc_id]
    blocks = document.get("blocks") or []
    title = document.get("title") or ""
    sample_parts: list[str] = []
    for block in blocks[:6]:
        if block.get("type") in ("paragraph", "abstract"):
            sample_parts.append(block_source_text(block))
        if sum(len(p) for p in sample_parts) > 2000:
            break
    terms = await translator.build_glossary(title, "\n".join(sample_parts))
    custom = str(db.get_settings().get("glossary") or "").strip()
    lines: list[str] = []
    if custom:
        lines.extend(line.strip() for line in custom.splitlines() if line.strip())
    for en, zh in terms.items():
        lines.append(f"{en} = {zh}")
    text = "；".join(dict.fromkeys(lines)) if lines else ""
    _GLOSSARY_CACHE[doc_id] = text
    return text


async def _run_ai(
    job: Job,
    document: dict[str, Any],
    blocks: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    translator: AITranslator,
    glossary_text: str,
    concurrency: int,
) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    title = document.get("title") or ""

    async def process(block: dict[str, Any]) -> None:
        if job.cancelled:
            return
        seq = int(block["seq"])
        segments = block_segments(block)
        masks: dict[int, list[str]] = {}
        items = []
        for index, segment in enumerate(segments):
            masked, tokens = mask_math(segment["src"])
            masks[index] = tokens
            items.append(
                {
                    "i": index,
                    "text": masked,
                    "kind": block_kind(block),
                }
            )
        if not items:
            return
        meta = _context_for(blocks, seq, glossary_text)
        meta["title"] = title
        async with semaphore:
            if job.cancelled:
                return
            try:
                try:
                    mapping = await translator.translate_items(items, meta)
                except TranslationError as exc:
                    # 首次请求遇到限流/超时/上游抖动时自动重试一次，避免用户
                    # 必须手动点“重译”。配置错误和额度不足不在自动重试范围。
                    if exc.code not in {
                        "rate_limit",
                        "timeout",
                        "network",
                        "upstream_error",
                        "bad_response",
                        "empty_result",
                    }:
                        raise
                    await asyncio.sleep(1.0)
                    mapping = await translator.translate_items(items, meta)
                translations = {
                    segment["sid"]: restore_math(
                        mapping.get(index, segment["src"]), masks.get(index, [])
                    )
                    for index, segment in enumerate(segments)
                }
                translation = _make_translation(job.engine, job.model, translations)
                db.save_block_translation(job.doc_id, seq, translation)
                job.done += 1
                job.consecutive_failures = 0
                job.updates.append({"seq": seq, "translation": translation})
            except TranslationError as exc:
                if exc.code in FATAL_CODES:
                    _fatal_stop(job, exc)
                    return
                job.failed += 1
                job.consecutive_failures += 1
                failure = {
                    "status": "error",
                    "engine": job.engine,
                    "model": job.model,
                    "segments": {},
                    "error": exc.to_dict(),
                    "updated_at": db.now(),
                }
                db.save_block_translation(job.doc_id, seq, failure)
                job.updates.append({"seq": seq, "translation": failure})
                if job.error is None:
                    job.error = exc.to_dict()
                if job.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    _fatal_stop(job, exc)
                    return
            except Exception as exc:
                job.failed += 1
                job.consecutive_failures += 1
                failure = {
                    "status": "error",
                    "engine": job.engine,
                    "model": job.model,
                    "segments": {},
                    "error": error_payload(exc),
                    "updated_at": db.now(),
                }
                db.save_block_translation(job.doc_id, seq, failure)
                job.updates.append({"seq": seq, "translation": failure})
                if job.error is None:
                    job.error = error_payload(exc)

    async def worker(queue: asyncio.Queue) -> None:
        while True:
            try:
                block = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await process(block)

    queue: asyncio.Queue = asyncio.Queue()
    for target in targets:
        queue.put_nowait(target)
    workers = [asyncio.create_task(worker(queue)) for _ in range(min(concurrency, len(targets)))]
    await asyncio.gather(*workers)


async def _run_free(
    job: Job,
    blocks: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    translator: FreeTranslator,
) -> None:
    semaphore = asyncio.Semaphore(3)

    async def process(block: dict[str, Any]) -> None:
        if job.cancelled:
            return
        seq = int(block["seq"])
        segments = block_segments(block)
        if not segments:
            return
        async with semaphore:
            if job.cancelled:
                return
            try:
                texts = []
                masks: list[list[str]] = []
                for segment in segments:
                    masked, tokens = mask_math(segment["src"])
                    texts.append(masked)
                    masks.append(tokens)
                translated = await translator.translate(texts)
                mapping = {
                    segment["sid"]: restore_math(text, masks[index])
                    for index, (segment, text) in enumerate(zip(segments, translated))
                    if text
                }
                translation = _make_translation(job.engine, job.model, mapping)
                db.save_block_translation(job.doc_id, seq, translation)
                job.done += 1
                job.consecutive_failures = 0
                job.updates.append({"seq": seq, "translation": translation})
            except TranslationError as exc:
                if exc.code in FATAL_CODES:
                    _fatal_stop(job, exc)
                    return
                job.failed += 1
                job.consecutive_failures += 1
                failure = {
                    "status": "error",
                    "engine": job.engine,
                    "model": job.model,
                    "segments": {},
                    "error": exc.to_dict(),
                    "updated_at": db.now(),
                }
                db.save_block_translation(job.doc_id, seq, failure)
                job.updates.append({"seq": seq, "translation": failure})
                if job.error is None:
                    job.error = exc.to_dict()
                if job.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    _fatal_stop(job, exc)
                    return
            except Exception as exc:
                job.failed += 1
                job.consecutive_failures += 1
                failure = {
                    "status": "error",
                    "engine": job.engine,
                    "model": job.model,
                    "segments": {},
                    "error": error_payload(exc),
                    "updated_at": db.now(),
                }
                db.save_block_translation(job.doc_id, seq, failure)
                job.updates.append({"seq": seq, "translation": failure})
                if job.error is None:
                    job.error = error_payload(exc)

    await asyncio.gather(*(process(block) for block in targets))


async def retranslate_block(doc_id: str, seq: int, mode: str | None = None) -> dict[str, Any]:
    document = db.get_document(doc_id)
    if not document:
        raise TranslationError("parse_error", message="文档不存在")
    settings = db.get_settings()
    mode = mode or str(settings.get("engine_mode") or "free")
    blocks = document.get("blocks") or []
    block = next((b for b in blocks if int(b["seq"]) == seq), None)
    if block is None:
        raise TranslationError("parse_error", message="段落不存在")
    if block.get("type") in SKIP_TYPES:
        raise TranslationError(
            "empty_result",
            message="页眉不参与翻译" if block.get("type") == "header" else "公式保持原样，不参与翻译",
        )
    segments = block_segments(block)
    if not segments:
        raise TranslationError("empty_result", message="该段没有可翻译的文字")
    engine, model = engine_label(mode, settings)

    if mode == "ai":
        translator: AITranslator | FreeTranslator = build_ai(settings)
        glossary_text = await _glossary_for(document, translator)
        meta = _context_for(blocks, seq, glossary_text)
        meta["title"] = document.get("title") or ""
        masks: dict[int, list[str]] = {}
        items = []
        for index, segment in enumerate(segments):
            masked, tokens = mask_math(segment["src"])
            masks[index] = tokens
            items.append({"i": index, "text": masked, "kind": block_kind(block)})
        try:
            try:
                mapping = await translator.translate_items(items, meta)
            except TranslationError as exc:
                if exc.code not in {
                    "rate_limit",
                    "timeout",
                    "network",
                    "upstream_error",
                    "bad_response",
                    "empty_result",
                }:
                    raise
                await asyncio.sleep(1.0)
                mapping = await translator.translate_items(items, meta)
        finally:
            await translator.aclose()
        translations = {
            segment["sid"]: restore_math(
                mapping.get(index, segment["src"]), masks.get(index, [])
            )
            for index, segment in enumerate(segments)
        }
    else:
        translator = build_free(settings)
        texts = []
        masks_list: list[list[str]] = []
        for segment in segments:
            masked, tokens = mask_math(segment["src"])
            texts.append(masked)
            masks_list.append(tokens)
        try:
            translated = await translator.translate(texts)
        finally:
            await translator.aclose()
        mapping = {index: text for index, text in enumerate(translated)}
        translations = {
            segment["sid"]: restore_math(
                mapping.get(index, segment["src"]), masks_list[index]
            )
            for index, segment in enumerate(segments)
        }
    translation = _make_translation(engine, model, translations)
    db.save_block_translation(doc_id, seq, translation)
    return translation