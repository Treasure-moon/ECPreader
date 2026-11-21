"""解析器入口：根据扩展名分发到 PDF / DOCX。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import normalize_text

SUPPORTED = {".pdf", ".docx"}


class ParseError(ValueError):
    pass


def parse_document(path: Path, filename: str) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        from .pdf_parser import parse_pdf

        result = parse_pdf(path, filename)
    elif suffix == ".docx":
        from .docx_parser import parse_docx

        result = parse_docx(path, filename)
    elif suffix == ".doc":
        raise ParseError(
            "暂不支持旧版 .doc 格式。请用 Word 另存为 .docx 后再拖入（Word 菜单：文件 → 另存为 → Word 文档）。"
        )
    else:
        raise ParseError(f"不支持的文件类型：{suffix}。目前支持 PDF 与 .docx。")

    if not result.get("blocks"):
        raise ParseError("没有从文件中提取到文本。可能是扫描版 PDF（纯图片），需要先做 OCR。")

    total_chars = sum(
        len(b.get("text", ""))
        + sum(len(c["src"]) for row in (b.get("cells") or []) for c in row)
        for b in result["blocks"]
    )
    if total_chars < 20:
        raise ParseError("提取到的文字太少，可能是扫描版或加密文档，暂无法翻译。")
    result["title"] = normalize_text(result.get("title") or filename)[:300]
    return result