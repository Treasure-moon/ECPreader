"""Word (.docx) 解析：保留标题、段落、列表、表格、公式与图表说明。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as _Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from . import common

HEADING_RE = re.compile(r"^(heading|标题)\s*([1-9])$", re.IGNORECASE)


def _iter_block_items(parent) -> Any:
    if isinstance(parent, _Document):
        element = parent.element.body
    else:
        element = parent._tc
    for child in element.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _has_equation(paragraph: Paragraph) -> bool:
    try:
        for node in paragraph._p.iter():
            if isinstance(node.tag, str) and node.tag.endswith("}oMath"):
                return True
    except Exception:
        return False
    return False


def _has_numbering(paragraph: Paragraph) -> bool:
    try:
        properties = paragraph._p.pPr
        return properties is not None and properties.numPr is not None
    except Exception:
        return False


def _cell_text(cell) -> str:
    parts = []
    for paragraph in cell.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _classify_paragraph(paragraph: Paragraph) -> tuple[str, int]:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    text = (paragraph.text or "").strip()
    heading_match = HEADING_RE.match(style.strip())
    if heading_match:
        return "heading", int(heading_match.group(2))
    if style.lower() in ("title", "subtitle"):
        return "title", 0
    if style.lower() == "caption" or common.CAPTION_RE.match(text):
        return "caption", 0
    if _has_equation(paragraph) and len(text) <= 200:
        return "formula", 0
    if "list" in style.lower() or _has_numbering(paragraph):
        return "list", 0
    return "paragraph", 0


def parse_docx(path: Path, filename: str) -> dict[str, Any]:
    document = Document(str(path))
    blocks: list[dict[str, Any]] = []
    heading_stack: list[str] = []
    in_references = False
    seen_title = False

    for item in _iter_block_items(document):
        if isinstance(item, Table):
            rows: list[list[str]] = []
            for row in item.rows:
                cells = [_cell_text(cell) for cell in row.cells]
                # 合并单元格会被 python-docx 重复返回，去掉相邻重复
                deduped: list[str] = []
                for cell in cells:
                    if deduped and cell and deduped[-1] == cell:
                        continue
                    deduped.append(cell)
                if any(deduped):
                    rows.append(deduped)
            if rows:
                blocks.append(
                    {
                        "type": "table",
                        "level": 0,
                        "page": None,
                        "text": "",
                        "note": "表格",
                        "cells": rows,
                    }
                )
            continue

        paragraph: Paragraph = item
        text = (paragraph.text or "").strip()
        if not text:
            continue
        if common.REFERENCE_HEADING_RE.match(text):
            in_references = True
            blocks.append(
                {
                    "type": "heading",
                    "level": 1,
                    "page": None,
                    "text": text,
                    "note": "",
                }
            )
            continue

        if in_references:
            blocks.append(
                {
                    "type": "reference",
                    "level": 0,
                    "page": None,
                    "text": text,
                    "note": "参考文献",
                }
            )
            continue

        block_type, level = _classify_paragraph(paragraph)
        if not seen_title and block_type == "title":
            blocks.append(
                {
                    "type": "title",
                    "level": 0,
                    "page": None,
                    "text": text,
                    "note": "标题",
                }
            )
            seen_title = True
            continue
        if not seen_title and not heading_stack and block_type == "heading" and level == 1:
            # 首个一级标题之前通常还有个文档标题
            pass
        if block_type == "heading":
            heading_stack = heading_stack[: max(level - 1, 0)]
        blocks.append(
            {
                "type": block_type,
                "level": level,
                "page": None,
                "text": text,
                "note": "",
            }
        )

    common.attach_segments(blocks)
    toc = common.build_toc(blocks)

    title = ""
    try:
        title = (document.core_properties.title or "").strip()
    except Exception:
        title = ""
    if not title:
        first_title = next((b["text"] for b in blocks if b["type"] == "title"), "")
        title = first_title
    if not title:
        title = Path(filename).stem

    return {
        "title": title,
        "blocks": blocks,
        "toc": toc,
        "page_count": None,
        "meta": {"format": "docx", "paragraphs": len(blocks)},
    }