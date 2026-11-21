"""PDF 解析：基于 PyMuPDF，保留标题/段落/图表说明/公式/参考文献结构。"""
from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any

import pymupdf

from . import common

# 页面顶/底部边缘区域，通常是页眉页脚
MARGIN_TOP = 0.075
MARGIN_BOTTOM = 0.925


def _join_lines(lines: list[str]) -> str:
    text = ""
    for line in lines:
        if not text:
            text = line
        elif text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text += " " + line
    return text.strip()


def _extract_blocks(page: pymupdf.Page) -> list[dict[str, Any]]:
    """抽取一页中的文本块与表格。"""
    items: list[dict[str, Any]] = []
    width, height = page.rect.width, page.rect.height

    table_rects: list[Any] = []
    try:
        finder = page.find_tables()
        for table in finder.tables:
            try:
                rows = table.extract()
            except Exception:
                continue
            rows = [[(cell or "").strip() for cell in row] for row in rows]
            rows = [row for row in rows if any(row)]
            if not rows:
                continue
            max_cols = max(len(row) for row in rows)
            rows = [row + [""] * (max_cols - len(row)) for row in rows]
            keep = [i for i in range(max_cols) if any(row[i] for row in rows)]
            rows = [[row[i] for i in keep] for row in rows]
            if len(rows) < 2 and len(keep) < 2:
                continue
            table_rects.append(pymupdf.Rect(table.bbox))
            items.append(
                {
                    "kind": "table",
                    "bbox": tuple(table.bbox),
                    "rows": rows,
                    "is_margin": False,
                }
            )
    except Exception:
        table_rects = []

    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = tuple(block["bbox"])
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        if any(
            tr.x0 - 1 <= cx <= tr.x1 + 1 and tr.y0 - 1 <= cy <= tr.y1 + 1
            for tr in table_rects
        ):
            continue

        lines: list[str] = []
        fonts: set[str] = set()
        sizes: list[float] = []
        chars = 0
        bold_chars = 0
        italic_chars = 0
        for line in block.get("lines", []):
            parts: list[str] = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text:
                    continue
                if text.strip():
                    fonts.add(str(span.get("font", "")))
                    sizes.append(float(span.get("size", 0)))
                    length = len(text)
                    chars += length
                    flags = int(span.get("flags", 0))
                    if flags & 16:
                        bold_chars += length
                    if flags & 2:
                        italic_chars += length
                parts.append(text)
            line_text = "".join(parts).strip()
            if line_text:
                lines.append(line_text)
        text = _join_lines(lines)
        if not text:
            continue
        size = max(sizes) if sizes else 0.0
        items.append(
            {
                "kind": "text",
                "bbox": bbox,
                "text": text,
                "size": round(size, 2),
                "chars": chars,
                "fonts": fonts,
                "bold_ratio": bold_chars / chars if chars else 0.0,
                "italic_ratio": italic_chars / chars if chars else 0.0,
                "is_margin": bbox[3] < height * MARGIN_TOP
                or bbox[1] > height * MARGIN_BOTTOM,
            }
        )
    return items


def _is_page_number(text: str) -> bool:
    stripped = text.strip()
    if re.fullmatch(r"\d{1,4}", stripped):
        return True
    if re.fullmatch(r"[ivxlcdm]{1,6}", stripped, re.IGNORECASE):
        return True
    if re.fullmatch(r"(page|p\.)\s*\d+", stripped, re.IGNORECASE):
        return True
    return False


def _order_items(items: list[dict[str, Any]], page_width: float) -> list[dict[str, Any]]:
    """按阅读顺序排列：双栏论文先左栏后右栏，跨栏块作为分隔。"""
    mid = page_width / 2.0

    def column(item: dict[str, Any]) -> int:
        x0, _, x1, _ = item["bbox"]
        if x1 - x0 > page_width * 0.66:
            return -1
        if x1 <= mid + 10:
            return 0
        if x0 >= mid - 10:
            return 1
        return -1

    left = [it for it in items if column(it) == 0]
    right = [it for it in items if column(it) == 1]
    two_column = len(left) >= 2 and len(right) >= 2

    if not two_column:
        return sorted(items, key=lambda it: (round(it["bbox"][1], 1), it["bbox"][0]))

    fulls = sorted(
        [it for it in items if column(it) == -1],
        key=lambda it: (it["bbox"][1], it["bbox"][0]),
    )

    def sort_key(it: dict[str, Any]) -> tuple[float, float]:
        return (round(it["bbox"][1], 1), it["bbox"][0])

    ordered: list[dict[str, Any]] = []
    prev_y = float("-inf")
    for full in fulls:
        y = full["bbox"][1]
        ordered.extend(sorted([it for it in left if prev_y <= it["bbox"][1] < y], key=sort_key))
        ordered.extend(sorted([it for it in right if prev_y <= it["bbox"][1] < y], key=sort_key))
        ordered.append(full)
        prev_y = y
    ordered.extend(sorted([it for it in left if it["bbox"][1] >= prev_y], key=sort_key))
    ordered.extend(sorted([it for it in right if it["bbox"][1] >= prev_y], key=sort_key))

    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for item in ordered:
        if id(item) in seen:
            continue
        seen.add(id(item))
        unique.append(item)
    return unique


def parse_pdf(path: Path, filename: str) -> dict[str, Any]:
    document = pymupdf.open(str(path))
    try:
        pages_raw: list[list[dict[str, Any]]] = []
        page_sizes: list[tuple[float, float]] = []
        for page in document:
            page_sizes.append((page.rect.width, page.rect.height))
            pages_raw.append(_extract_blocks(page))

        # 统计正文主字号（按字符数加权）
        size_weights: dict[float, int] = {}
        margin_texts: dict[str, int] = {}
        for items in pages_raw:
            for item in items:
                if item["kind"] == "text" and not item["is_margin"] and item["chars"] >= 20:
                    size_weights[item["size"]] = size_weights.get(item["size"], 0) + item["chars"]
                if item["kind"] == "text" and item["is_margin"] and len(item["text"]) < 120:
                    key = item["text"].strip().lower()
                    margin_texts[key] = margin_texts.get(key, 0) + 1
        body_size = 10.0
        if size_weights:
            body_size = max(size_weights.items(), key=lambda kv: kv[1])[0]
        page_total = max(len(pages_raw), 1)
        repeated_margins = {
            text for text, count in margin_texts.items() if count >= max(2, page_total * 0.4)
        }

        blocks: list[dict[str, Any]] = []
        toc: list[dict[str, Any]] = []
        in_references = False
        seen_title = False

        for page_index, items in enumerate(pages_raw, start=1):
            width, _ = page_sizes[page_index - 1]
            ordered = _order_items(items, width)
            for item in ordered:
                item_layout = {
                    "bbox": [round(float(v), 2) for v in item["bbox"]],
                    "size": round(float(item.get("size") or 0.0), 2),
                }
                if item["kind"] == "table":
                    blocks.append(
                        {
                            **item_layout,
                            "type": "table",
                            "level": 0,
                            "page": page_index,
                            "text": "",
                            "note": "表格",
                            "cells": item["rows"],
                        }
                    )
                    continue

                text = item["text"].strip()
                if not text:
                    continue
                if item["is_margin"]:
                    lowered = text.lower()
                    if (
                        _is_page_number(text)
                        or lowered in repeated_margins
                        or len(text) < 8
                    ):
                        continue

                if in_references:
                    blocks.append(
                        {
                            **item_layout,
                            "type": "reference",
                            "level": 0,
                            "page": page_index,
                            "text": text,
                            "note": "参考文献",
                        }
                    )
                    continue

                if common.REFERENCE_HEADING_RE.match(text):
                    in_references = True
                    blocks.append(
                        {
                            **item_layout,
                            "type": "heading",
                            "level": 1,
                            "page": page_index,
                            "text": text,
                            "note": "",
                        }
                    )
                    continue

                if common._looks_like_formula(text, item["fonts"], item["italic_ratio"]):
                    blocks.append(
                        {
                            **item_layout,
                            "type": "formula",
                            "level": 0,
                            "page": page_index,
                            "text": text,
                            "note": "公式",
                        }
                    )
                    continue

                is_caption = bool(common.CAPTION_RE.match(text))
                heading, level = common._looks_like_heading(
                    text, item["size"], body_size, item["bold_ratio"]
                )

                if not seen_title and page_index == 1 and item["size"] >= body_size * 1.28:
                    blocks.append(
                        {
                            **item_layout,
                            "type": "title",
                            "level": 0,
                            "page": page_index,
                            "text": text,
                            "note": "标题",
                        }
                    )
                    seen_title = True
                    continue

                if is_caption:
                    block_type = "caption"
                    level = 0
                elif heading:
                    block_type = "heading"
                elif re.match(r"^\s*[•\-\u2022]\s+", text) or re.match(
                    r"^\s*\(\d+\)\s+", text
                ):
                    block_type = "list"
                else:
                    block_type = "paragraph"
                    level = 0

                blocks.append(
                    {
                        **item_layout,
                        "type": block_type,
                        "level": level,
                        "page": page_index,
                        "text": text,
                        "note": "",
                    }
                )

        # 清理：去掉空文本块（表格除外）
        blocks = [
            b for b in blocks if b["type"] == "table" or (b.get("text") or "").strip()
        ]
        common.attach_segments(blocks)
        toc = common.build_toc(blocks)

        meta_title = ""
        try:
            meta_title = (document.metadata or {}).get("title") or ""
        except Exception:
            meta_title = ""
        title = _clean_title(meta_title)
        if not title and blocks and blocks[0]["type"] == "title":
            title = blocks[0]["text"]
        if not title:
            title = Path(filename).stem

        pages_meta = [
            {"n": index, "w": round(width, 2), "h": round(height, 2)}
            for index, (width, height) in enumerate(page_sizes, start=1)
        ]
        return {
            "title": title,
            "blocks": blocks,
            "toc": toc,
            "page_count": len(pages_raw),
            "layout": "abs",
            "meta": {
                "body_size": body_size,
                "pages": len(pages_raw),
                "format": "pdf",
                "pages_meta": pages_meta,
            },
        }
    finally:
        document.close()



def _clean_title(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"untitled", "microsoft word", "document", "无标题"}:
        return ""
    if len(text) < 4:
        return ""
    return text