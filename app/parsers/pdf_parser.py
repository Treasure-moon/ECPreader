"""PDF 解析：基于 PyMuPDF，保留标题/段落/图表说明/公式/参考文献结构。"""
from __future__ import annotations

import re
import statistics
from html import unescape
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


def _union_bbox(first: list[float], second: list[float]) -> list[float]:
    return [
        round(min(first[0], second[0]), 2),
        round(min(first[1], second[1]), 2),
        round(max(first[2], second[2]), 2),
        round(max(first[3], second[3]), 2),
    ]


def _merge_title_lines(blocks: list[dict[str, Any]], body_size: float) -> None:
    """把被 PDF 拆成多个文本块的论文标题合并回一个标题。

    很多 IEEE 模板的标题会按行拆块；原逻辑只把第一块标成 title，其余行
    被当成 heading，导致一个标题在目录里出现好几次。
    """
    title_index = next(
        (index for index, block in enumerate(blocks) if block.get("type") == "title"),
        None,
    )
    if title_index is None:
        return
    title = blocks[title_index]
    index = title_index + 1
    while index < len(blocks):
        nxt = blocks[index]
        if nxt.get("page") != title.get("page"):
            break
        if nxt.get("type") not in ("heading", "title"):
            break
        if float(nxt.get("size") or 0) < body_size * 1.2:
            break
        if common.HEADING_MARKER_RE.match(nxt.get("text") or ""):
            break
        if not title.get("bbox") or not nxt.get("bbox"):
            break
        title["text"] = f"{title.get('text', '').strip()} {nxt.get('text', '').strip()}".strip()
        title["bbox"] = _union_bbox(list(title["bbox"]), list(nxt["bbox"]))
        title["size"] = max(float(title.get("size") or 0), float(nxt.get("size") or 0))
        del blocks[index]


def _is_heading_continuation(heading: dict[str, Any], nxt: dict[str, Any]) -> bool:
    text = (nxt.get("text") or "").strip()
    current = (heading.get("text") or "").strip()
    if not text or not current:
        return False
    if nxt.get("page") != heading.get("page"):
        return False
    if nxt.get("type") not in ("paragraph", "heading"):
        return False
    if abs(float(nxt.get("size") or 0) - float(heading.get("size") or 0)) > 0.8:
        return False
    if current[-1:] in ".,;:?!":
        return False
    if text[-1:] in ".,;:?!" or len(text.split()) > 12:
        return False
    if common.HEADING_MARKER_RE.match(text):
        return False
    letters = [ch for ch in text if ch.isalpha()]
    if not letters or not text.isupper():
        return False
    return len(text) <= 100


def _merge_heading_continuations(blocks: list[dict[str, Any]]) -> None:
    """合并跨文本块的章节标题，如 “II. ... POINT” + “SET REGISTRATION METHOD”。"""
    index = 0
    while index < len(blocks) - 1:
        block = blocks[index]
        nxt = blocks[index + 1]
        if block.get("type") == "heading" and _is_heading_continuation(block, nxt):
            block["text"] = f"{block.get('text', '').strip()} {nxt.get('text', '').strip()}".strip()
            if block.get("bbox") and nxt.get("bbox"):
                block["bbox"] = _union_bbox(list(block["bbox"]), list(nxt["bbox"]))
            del blocks[index + 1]
            continue
        index += 1


# 页面上/下边缘这一段通常是页眉页脚（页码、刊名、期刊卷期）。
PAGE_BAND = 0.12


def _demoted_type(text: str, level: int) -> str | None:
    """给「被误判成标题」的块挑一个更合适的类型；不是误判就返回 None。"""
    stripped = (text or "").strip()
    if not stripped:
        return "paragraph"
    if common.TOC_CAPTION_RE.match(stripped) or common.CAPTION_RE.match(stripped):
        return "caption"
    if any(ch in common.TOC_MATH_CHARS for ch in stripped):
        return "formula"
    # 作者行：一串逗号分隔的姓名 + 单位角标。
    if stripped.count(",") >= 4:
        return "paragraph"
    words = re.findall(r"[A-Za-z]{2,}", stripped)
    cjk = bool(re.search(r"[\u4e00-\u9fff]", stripped))
    if not words and not cjk:
        # “1 N”“(a)”“12” 这类图片刻度或子图标签，不是章节。
        return "formula"
    if common.looks_like_tick_label(stripped):
        return "paragraph"
    return None


def _prune_heading_noise(
    blocks: list[dict[str, Any]], page_heights: list[float]
) -> None:
    """把误判成章节标题的页眉、图注、公式残片、作者行改回正常类型。

    目录只收 heading/title，所以这里必须就地改类型，阅读区也跟着一起干净。
    """
    heights = {index + 1: height for index, height in enumerate(page_heights)}

    def in_band(block: dict[str, Any]) -> bool:
        height = heights.get(block.get("page")) or 0.0
        bbox = block.get("bbox") or []
        if height <= 0 or len(bbox) < 4:
            return False
        ratio = float(bbox[1]) / height
        return ratio <= PAGE_BAND or ratio >= 1 - PAGE_BAND

    # 1) 每页重复的页眉：文字归一化后出现在 ≥3 页的上下边缘。
    #    页眉可能被识别成 heading、paragraph 甚至 reference，所以按文字判断，
    #    不限定原来的类型；只要求短（页眉不会很长）且落在页边。
    page_sets: dict[str, set] = {}
    for block in blocks:
        if block.get("type") in ("table", "formula") or not in_band(block):
            continue
        text = (block.get("text") or "").strip()
        if len(text) > 160:
            continue
        key = common.loose_toc_key(text)
        if len(key) >= 8:
            page_sets.setdefault(key, set()).add(block.get("page"))
    running = {key for key, pages in page_sets.items() if len(pages) >= 3}

    # 2) 论文标题上方那一行（期刊名、卷期、DOI）同样是页眉。
    title_index = next(
        (index for index, block in enumerate(blocks) if block.get("type") == "title"),
        None,
    )

    for index, block in enumerate(blocks):
        if block.get("type") in ("table", "formula"):
            continue
        text = block.get("text") or ""
        key = common.loose_toc_key(text)
        if key in running and in_band(block):
            if block.get("type") != "title":
                block["type"] = "header"
                block["level"] = 0
                block["note"] = "页眉"
            continue
        if block.get("type") not in ("heading", "title"):
            continue
        if block.get("type") == "title":
            continue
        if title_index is not None and index < title_index and in_band(block):
            block["type"] = "header"
            block["level"] = 0
            block["note"] = "页眉"
            continue
        demoted = _demoted_type(text, int(block.get("level") or 0))
        if demoted:
            block["type"] = demoted
            block["level"] = 0


AUTHOR_AFFIX_RE = re.compile(
    r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?<![A-Za-z])[a-z](?![a-z])"
    r"(?:\s*,\s*[a-z](?![a-z]))?\s*\*?"
)


def _looks_like_author_line(text: str) -> bool:
    """作者行：“Zhiyuan Li a, Fengxiang Jin a,b,*, ...”。

    必须真的是「姓名 + 单字母单位角标」，否则正文里的 “The authors …” 之类
    也会被当成作者行。
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 400:
        return False
    if stripped.count(",") < 2:
        return False
    return len(AUTHOR_AFFIX_RE.findall(stripped)) >= 2


def _pick_title_item(
    items: list[dict[str, Any]], body_size: float, page_width: float
) -> tuple | None:
    """在第一页里挑论文标题。

    “第一个字号够大的块”在期刊版式里很容易落到刊名/页眉上（Elsevier 的刊名
    甚至比标题还大一点）。标题紧挨着作者行，用这一点当主要判据。
    """
    candidates: list[tuple[float, int, tuple]] = []
    for index, item in enumerate(items):
        if item["kind"] != "text" or item["is_margin"]:
            continue
        text = (item.get("text") or "").strip()
        if len(text) < 10 or len(text) > 300:
            continue
        if common.CAPTION_RE.match(text) or common.TOC_CAPTION_RE.match(text):
            continue
        # 带章节编号的块一定是章节标题；首字下沉还会把字号顶到很大。
        if common.HEADING_MARKER_RE.match(text) or common._heading_marker_level(text):
            continue
        if float(item.get("size") or 0) < body_size * 1.2:
            continue
        if text.lower().rstrip(".:") in common.SECTION_NAMES:
            continue
        bbox = item["bbox"]
        if page_width > 0 and (float(bbox[2]) - float(bbox[0])) < page_width * 0.35:
            continue
        score = float(item.get("size") or 0) / max(body_size, 1.0) + len(text) / 200.0
        for follower in items[index + 1 : index + 4]:
            if follower["kind"] == "text" and _looks_like_author_line(
                follower.get("text") or ""
            ):
                score += 12.0
                break
        candidates.append((score, index, tuple(float(v) for v in bbox)))
    if not candidates:
        return None
    candidates.sort(key=lambda entry: (-entry[0], entry[1]))
    return candidates[0][2]


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
        title_bbox = (
            _pick_title_item(pages_raw[0], body_size, page_sizes[0][0])
            if pages_raw
            else None
        )

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

                text = common.clean_heading_text(item["text"].strip())
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

                if (
                    not seen_title
                    and page_index == 1
                    and title_bbox is not None
                    and tuple(float(v) for v in item["bbox"]) == title_bbox
                ):
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

                is_caption = bool(common.CAPTION_RE.match(text))
                heading, level = common._looks_like_heading(
                    text, item["size"], body_size, item["bold_ratio"]
                )

                if not heading and common._looks_like_formula(
                    text, item["fonts"], item["italic_ratio"]
                ):
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
        _merge_title_lines(blocks, body_size)
        _merge_heading_continuations(blocks)
        _prune_heading_noise(blocks, [height for _, height in page_sizes])
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
                "parser_version": 5,
                "pages_meta": pages_meta,
            },
        }
    finally:
        document.close()



def _clean_title(raw: str) -> str:
    # 有些 PDF 的元数据标题是 XML 转义过的（例如把连接号写成 &#x2013;），
    # 直接存进数据库会让标签页和首页显示成实体文本。
    text = unescape(raw or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"untitled", "microsoft word", "document", "无标题"}:
        return ""
    if len(text) < 4:
        return ""
    return text