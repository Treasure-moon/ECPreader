"""PDF 原版布局支持：页面位图渲染 + 按坐标取词。

原版视图不重排正文，而是把原始页面渲染成图片，再按解析出的坐标覆盖译文。
这样图、公式、分栏、字体都保持论文原样。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pymupdf

from . import config

DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _check_id(doc_id: str) -> str:
    if not DOC_ID_RE.match(doc_id or ""):
        raise ValueError("文档 ID 不合法")
    return doc_id


def page_png_path(doc_id: str, page_no: int, dpi: int) -> Path:
    return config.PAGE_DIR / _check_id(doc_id) / f"p{page_no}-{dpi}.png"


def render_page_png(doc_path: Path, doc_id: str, page_no: int, dpi: int = 160) -> Path:
    """把某一页渲染为 PNG（带磁盘缓存）。"""
    dpi = max(96, min(240, int(dpi or 160)))
    target = page_png_path(doc_id, page_no, dpi)
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(doc_path)) as document:
        if page_no < 1 or page_no > document.page_count:
            raise ValueError("页码超出范围")
        page = document[page_no - 1]
        pixmap = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
        tmp = target.with_name(f"{target.stem}.{os.getpid()}.png")
        pixmap.save(str(tmp))
    os.replace(tmp, target)
    return target


def clear_page_cache(doc_id: str) -> None:
    try:
        folder = config.PAGE_DIR / _check_id(doc_id)
    except ValueError:
        return
    if not folder.exists():
        return
    for child in folder.glob("*.png"):
        child.unlink(missing_ok=True)
    try:
        folder.rmdir()
    except OSError:
        pass


def _clean_word(text: str) -> str:
    return (text or "").strip().strip(".,;:!?()[]{}<>\"'`“”‘’—–-·")


def word_at(doc_path: Path, page_no: int, x: float, y: float) -> dict[str, Any] | None:
    """返回点击位置处的英文单词及其包围盒（PDF 坐标）。"""
    with pymupdf.open(str(doc_path)) as document:
        if page_no < 1 or page_no > document.page_count:
            return None
        page = document[page_no - 1]
        words = page.get_text("words")
    hit: tuple[str, tuple[float, float, float, float]] | None = None
    nearest: tuple[float, str, tuple[float, float, float, float]] | None = None
    for item in words:
        x0, y0, x1, y1 = (float(item[0]), float(item[1]), float(item[2]), float(item[3]))
        text = _clean_word(str(item[4]))
        if not text:
            continue
        if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= y <= y1 + 2:
            hit = (text, (x0, y0, x1, y1))
            break
        cx = min(max(x, x0), x1)
        cy = min(max(y, y0), y1)
        distance = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
        if nearest is None or distance < nearest[0]:
            nearest = (distance, text, (x0, y0, x1, y1))
    if hit is None and nearest is not None and nearest[0] <= 16:
        hit = (nearest[1], nearest[2])
    if hit is None:
        return None
    word, bbox = hit
    if not re.search(r"[A-Za-z]", word):
        return None
    return {"word": word, "bbox": list(bbox)}


def block_at(blocks: list[dict[str, Any]], page_no: int, x: float, y: float) -> dict[str, Any] | None:
    """找到包含坐标的文本块（按面积从小到大，优先小块）。"""
    candidates: list[tuple[float, dict[str, Any]]] = []
    for block in blocks:
        if block.get("page") != page_no:
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        x0, y0, x1, y1 = bbox
        if x0 - 3 <= x <= x1 + 3 and y0 - 3 <= y <= y1 + 3:
            candidates.append(((x1 - x0) * (y1 - y0), block))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def sentence_around(paragraph: str, word: str) -> str:
    """从段落里截出包含该词的句子（找不到就返回段落开头）。"""
    text = paragraph or ""
    if not text.strip():
        return word
    pattern = re.compile(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return text[:300]
    start = match.start()
    end = match.end()
    while start > 0 and text[start - 1] not in ".!?。！？\n":
        start -= 1
    while end < len(text) and text[end] not in ".!?。！？\n":
        end += 1
    if end < len(text) and text[end] in ".!?。！？":
        end += 1
    return text[start:end].strip()[:400]

TRANSLATABLE_TYPES = {"title", "heading", "paragraph", "caption", "list"}


def _segment_ranges(block: dict[str, Any]) -> list[tuple[int, int, str]]:
    """把 block.text 里的句子映射成字符区间，用于把单词归到句子上。"""
    text = block.get("text") or ""
    ranges: list[tuple[int, int, str]] = []
    cursor = 0
    for segment in block.get("segments") or []:
        sid = str(segment.get("sid") or "")
        source = (segment.get("src") or "").strip()
        if not source:
            continue
        index = text.find(source, cursor)
        if index < 0:
            index = text.find(source)
        if index < 0:
            index = cursor
        end = index + len(source)
        ranges.append((index, end, sid))
        cursor = max(cursor, end)
    return ranges


def _sid_at(ranges: list[tuple[int, int, str]], index: int) -> str | None:
    for start, end, sid in ranges:
        if start <= index < end:
            return sid
        if index < start:
            return ranges[0][2] if ranges else None
    return ranges[-1][2] if ranges else None


def page_words(doc_path: Path, page_no: int, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """返回页面上每个单词的位置和所属句号，供前端做可选中文本层。"""
    with pymupdf.open(str(doc_path)) as document:
        if page_no < 1 or page_no > document.page_count:
            raise ValueError("页码超出范围")
        page = document[page_no - 1]
        width, height = float(page.rect.width), float(page.rect.height)
        raw = page.get_text("words")

    prepared: list[tuple[list[float], dict[str, Any], list[tuple[int, int, str]]]] = []
    for block in blocks:
        if block.get("page") != page_no:
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        ranges = _segment_ranges(block) if block.get("type") in TRANSLATABLE_TYPES else []
        prepared.append((list(map(float, bbox)), block, ranges))

    cursors: dict[int, int] = {}
    words: list[dict[str, Any]] = []
    for item in raw:
        x0, y0, x1, y1 = (float(item[0]), float(item[1]), float(item[2]), float(item[3]))
        text = str(item[4] or "").strip()
        if not text:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        chosen: tuple[float, dict[str, Any], list[tuple[int, int, str]]] | None = None
        for bbox, block, ranges in prepared:
            if bbox[0] - 2 <= cx <= bbox[2] + 2 and bbox[1] - 2 <= cy <= bbox[3] + 2:
                area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                if chosen is None or area < chosen[0]:
                    chosen = (area, block, ranges)
        if chosen is None:
            continue
        block, ranges = chosen[1], chosen[2]
        seq = int(block.get("seq") or 0)
        source_text = block.get("text") or ""
        cursor = cursors.get(seq, 0)
        index = source_text.find(text, cursor)
        if index < 0 and text.endswith("-"):
            index = source_text.find(text.rstrip("-"), cursor)
        if index < 0:
            index = source_text.lower().find(text.lower(), cursor)
        if index < 0:
            index = source_text.find(text)
        if index >= 0:
            cursors[seq] = index + max(len(text), 1)
        sid = _sid_at(ranges, index) if ranges and index >= 0 else None
        words.append(
            {
                "x0": round(x0, 1),
                "y0": round(y0, 1),
                "x1": round(x1, 1),
                "y1": round(y1, 1),
                "w": text,
                "sid": sid,
                "seq": seq or None,
            }
        )
    return {"page": page_no, "width": round(width, 1), "height": round(height, 1), "words": words}
