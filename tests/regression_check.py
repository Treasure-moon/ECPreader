"""后端回归检查：目录、公式保护、翻译重试、旧译文 sid 对齐。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import _align_translation
from app.parsers import parse_document
from app.startup_guard import StartupGuard
from app.translate.errors import TranslationError
from app.translate.free import FreeTranslator
from app.translate.mathmask import mask_math, restore_math
from app.translate.pipeline import Job, _run_free

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "uploads" / "8a95514233f942f0a8872b5e83a03475.pdf"


def check_parser() -> None:
    if not PDF.exists():
        print("SKIP parser · 样例 PDF 不存在")
        return
    result = parse_document(PDF, PDF.name)
    toc = [entry["text"] for entry in result["toc"]]
    assert len(toc) >= 12, toc
    assert sum("With Multimember" in text for text in toc) == 1, toc
    assert any(text.startswith("I. INTRODUCTION") for text in toc), toc
    assert any(text.startswith("V. CONCLUSION") for text in toc), toc
    assert any("IMU PREINTEGRATION" in text for text in toc), toc
    assert not any(text.startswith("64 J") for text in toc), toc
    formulas = [block for block in result["blocks"] if block["type"] == "formula"]
    assert len(formulas) > 100, len(formulas)
    import re

    for block in formulas:
        assert len(re.findall(r"\b[a-z]{3,}\b", block["text"])) < 3, block["text"]
    print(f"PASS parser · {len(toc)} 条目录 / {len(formulas)} 个公式块")


def check_mathmask() -> None:
    samples = [
        "The point set P = {Pi} is transformed by R and T.",
        "where Xi,d = Ld + U(0,1) and α is a parameter.",
        "The complexity is O(T × NP × M × N).",
    ]
    for sample in samples:
        masked, tokens = mask_math(sample)
        assert "⟦M" in masked, masked
        assert restore_math(masked, tokens) == sample
    print("PASS mathmask · 公式变量保护与还原")


def check_db_alignment() -> None:
    block = {
        "type": "paragraph",
        "segments": [{"sid": "b6s0", "src": "one"}, {"sid": "b6s1", "src": "two"}],
    }
    translation = {
        "status": "done",
        "segments": {"b8s0": "一", "b8s1": "二"},
    }
    aligned = _align_translation(block, translation)
    assert aligned and aligned["segments"] == {"b6s0": "一", "b6s1": "二"}
    formula = {
        "type": "formula",
        "segments": [{"sid": "b6s0", "src": "x = 1"}],
    }
    assert _align_translation(formula, {"status": "done", "segments": {"b8s0": "x"}}) is None
    print("PASS db · 旧译文 sid 重映射 / 公式译文丢弃")


async def check_free_retry() -> None:
    translator = FreeTranslator(provider="mymemory", timeout=5)
    calls: list[str] = []

    async def flaky(provider: str, text: str) -> str:
        calls.append(text)
        if text.startswith("bad"):
            raise TranslationError("empty_result", "empty")
        return "译:" + text

    async def keep(provider: str, text: str, result: str) -> str:
        return result

    translator._translate_with = flaky  # type: ignore[method-assign]
    translator._ensure_translated = keep  # type: ignore[method-assign]
    try:
        result = await translator._translate_all("mymemory", ["good", "bad"])
    finally:
        await translator.aclose()
    assert result == ["译:good", "bad"], result
    assert calls.count("bad") == 2, calls
    print("PASS free · 单句软失败重试并回退原文")


def check_external_links() -> None:
    from fastapi import HTTPException

    from app.api import EXTERNAL_LINKS, open_external

    assert "https://github.com/Treasure-moon/ECPreader" in EXTERNAL_LINKS
    try:
        open_external({"url": "https://example.com"})
    except HTTPException as exc:
        assert exc.status_code == 400, exc
    else:
        raise AssertionError("任意外部链接应被拒绝")
    print("PASS external · 仅允许项目仓库、更新页与作者邮箱")


def check_version_refs() -> None:
    """版本号只有一个来源：README 的下载链接和构建说明都要跟 config.APP_VERSION 一致。"""
    import re

    from app.config import APP_VERSION

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = set(re.findall(r"ECPreader-Setup-([\d.]+)\.exe", readme))
    assert links, "README 里找不到安装包链接"
    assert links == {APP_VERSION}, f"README 里的安装包版本 {sorted(links)} != {APP_VERSION}"
    for name in ("app/__init__.py", "tools/ecp_version.txt"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert APP_VERSION in text, f"{name} 里的版本号没跟上 {APP_VERSION}"
    releases = (ROOT / "RELEASES.md").read_text(encoding="utf-8")
    assert f"## {APP_VERSION}" in releases, f"RELEASES.md 里没有 {APP_VERSION} 这一节"
    print(f"PASS version · README/RELEASES/版本文件都是 {APP_VERSION}")


def check_startup_guard() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / ".starting"
        first = StartupGuard(path)
        assert first.acquire()
        assert not StartupGuard(path).acquire(), "启动中的重复实例应被拒绝"
        first.release()
        second = StartupGuard(path)
        assert second.acquire(), "启动完成后应允许再次打开窗口"
        second.release()
        path.write_text(
            '{"pid": 99999999, "token": "stale", "at": 0}',
            encoding="utf-8",
        )
        third = StartupGuard(path)
        assert third.acquire(), "崩溃遗留的锁应自动过期"
        third.release()
    print("PASS startup · 启动中防重复 / 完成后可多开 / 过期锁自动清理")


def check_history_dedupe() -> None:
    import tempfile

    from app import config, db

    old = (config.DATA_DIR, config.UPLOAD_DIR, config.PAGE_DIR, config.DB_FILE, db._conn)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        config.DATA_DIR = root
        config.UPLOAD_DIR = root / "uploads"
        config.PAGE_DIR = root / "pages"
        config.DB_FILE = root / "app.db"
        db._conn = None
        try:
            first = db.add_history(
                doc_id="doc-1", title="论文", mode="free", engine="free:auto",
                model="", status="done", block_total=10,
            )
            second = db.add_history(
                doc_id="doc-1", title="论文", mode="ai", engine="ai:deepseek",
                model="deepseek-chat", status="done", block_total=10,
            )
            rows = db.list_history()
            assert len(rows) == 1, rows
            assert rows[0]["id"] == second and rows[0]["id"] != first, rows
        finally:
            if db._conn is not None:
                db._conn.close()
            db._conn = old[4]
            config.DATA_DIR, config.UPLOAD_DIR, config.PAGE_DIR, config.DB_FILE = old[:4]
    print("PASS history · 重新翻译只保留最后一条记录")


async def check_quota_stops() -> None:
    class QuotaTranslator:
        def __init__(self) -> None:
            self.calls = 0

        async def translate(self, texts):
            self.calls += 1
            raise TranslationError("free_quota")

    job = Job(
        id="quota", doc_id="doc", mode="free", engine="free:mymemory",
        model="", history_id="h", title="论文", total=20,
    )
    blocks = [
        {"seq": index, "type": "paragraph", "segments": [{"sid": f"b{index}s0", "src": "hello"}]}
        for index in range(20)
    ]
    translator = QuotaTranslator()
    await _run_free(job, blocks, blocks, translator)  # type: ignore[arg-type]
    assert job.status == "failed" and job.fatal_error, job
    assert job.error and job.error.get("code") == "free_quota", job.error
    assert translator.calls <= 3 and translator.calls < len(blocks), translator.calls
    print(f"PASS quota · 额度耗尽立即停止（仅 {translator.calls} 个并发请求）")


def main() -> int:
    check_parser()
    check_mathmask()
    check_db_alignment()
    check_external_links()
    check_version_refs()
    check_startup_guard()
    check_history_dedupe()
    asyncio.run(check_free_retry())
    asyncio.run(check_quota_stops())
    print("后端回归检查全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())