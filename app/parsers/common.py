"""解析层公共逻辑：清洗、分句、分块、目录生成。"""
from __future__ import annotations

import re
from typing import Any

LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\u00ad": "",
}

_WS_RE = re.compile(r"[ \t\u00a0]+")
_SOFT_HYPHEN_RE = re.compile(r"(\w)-\s*\n\s*(\w)")

CAPTION_RE = re.compile(
    r"^\s*(fig(?:ure)?\.?|table|tab\.?|scheme|algorithm|listing)\s*"
    r"([0-9]+[a-zA-Z]?|[IVXivx]+)\s*(?:[:.|]\s|\s*$)",
    re.IGNORECASE,
)

SECTION_NAMES = {
    "abstract",
    "introduction",
    "related work",
    "background",
    "method",
    "methods",
    "methodology",
    "approach",
    "experiments",
    "experimental setup",
    "results",
    "results and discussion",
    "discussion",
    "conclusion",
    "conclusions",
    "future work",
    "acknowledgments",
    "acknowledgements",
    "references",
    "bibliography",
    "appendix",
    "supplementary material",
}

REFERENCE_HEADING_RE = re.compile(
    r"^\s*(references|bibliography|literature cited|参考文献)\s*$", re.IGNORECASE
)

NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+[A-Z\u4e00-\u9fff]")

ABBREVIATIONS = {
    "e.g.",
    "i.e.",
    "et al.",
    "al.",
    "fig.",
    "figs.",
    "eq.",
    "eqs.",
    "sec.",
    "secs.",
    "tab.",
    "ref.",
    "refs.",
    "vs.",
    "cf.",
    "approx.",
    "no.",
    "nos.",
    "pp.",
    "p.",
    "dr.",
    "mr.",
    "mrs.",
    "ms.",
    "prof.",
    "st.",
    "inc.",
    "ltd.",
    "co.",
    "univ.",
    "dept.",
    "vol.",
    "ch.",
    "resp.",
    "ibid.",
    "etc.",
}

MATH_FONT_HINTS = (
    "cmmi",
    "cmsy",
    "cmex",
    "msam",
    "msbm",
    "symbol",
    "math",
    "mtmi",
    "mtsy",
    "stix",
    "euclid",
    "cambria math",
)

MATH_CHARS = set(
    "=≠≈≤≥±∓×÷∑∏∫∮√∞∂∇∈∉⊂⊃∪∩∀∃→←↔⇒⇔"
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
)


def normalize_text(text: str) -> str:
    for src, dst in LIGATURES.items():
        text = text.replace(src, dst)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _SOFT_HYPHEN_RE.sub(r"\1\2", text)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


def _is_abbreviation(tail: str) -> bool:
    tail = tail.strip()
    if not tail:
        return False
    last = tail.split()[-1].lower() if tail.split() else ""
    if last in ABBREVIATIONS:
        return True
    if re.match(r"^[a-z]\.$", last) or re.match(r"^[A-Z]\.$", tail.split()[-1]):
        return True
    return False


def split_sentences(text: str) -> list[str]:
    """将英文段落切分为句子，尽量保留学术写作中的缩写。"""
    text = normalize_text(text)
    if not text:
        return []
    sentences: list[str] = []
    start = 0
    pattern = re.compile(r"[.!?][\"'”’)\]]*(?=\s|$)")
    for match in pattern.finditer(text):
        end = match.end()
        if end <= start:
            continue
        piece = text[start:end].strip()
        if not piece:
            continue
        if _is_abbreviation(piece):
            continue
        rest = text[end:].lstrip()
        if rest and not (rest[0].isupper() or rest[0].isdigit() or rest[0] in "“\"(['"):
            continue
        sentences.append(piece)
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    remainder = text[start:].strip()
    if remainder:
        sentences.append(remainder)
    if not sentences:
        return [text]
    # 合并过短的残片（常见于公式、引用编号）
    merged: list[str] = []
    for piece in sentences:
        if merged and len(piece) < 12 and not piece[-1:] in ".!?":
            merged[-1] = (merged[-1] + " " + piece).strip()
        else:
            merged.append(piece)
    return merged


def _looks_like_heading(
    text: str, size: float, body_size: float, bold_ratio: float
) -> tuple[bool, int]:
    stripped = text.strip()
    if not stripped or len(stripped) > 160:
        return False, 0
    lowered = stripped.lower().rstrip(".:")
    if lowered in SECTION_NAMES and len(stripped) <= 60:
        return True, 1 if len(stripped.split()) <= 3 else 2
    numbered = NUMBERED_HEADING_RE.match(stripped)
    if numbered and len(stripped) < 120 and not stripped.endswith("."):
        depth = len(numbered.group(1).split("."))
        return True, min(depth, 4)
    if size >= body_size * 1.18 and not stripped.endswith((".", ",", ";")):
        if size >= body_size * 1.45:
            return True, 1
        if size >= body_size * 1.28:
            return True, 2
        return True, 3
    if (
        bold_ratio >= 0.7
        and len(stripped) <= 80
        and not stripped.endswith((".", ",", ";"))
        and stripped[:1].isupper()
        and size >= body_size * 0.98
    ):
        return True, 3
    return False, 0


def _looks_like_formula(text: str, fonts: set[str], italic_ratio: float) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 260:
        return False
    letters = sum(ch.isalpha() for ch in stripped)
    digits = sum(ch.isdigit() for ch in stripped)
    math_symbols = sum(ch in MATH_CHARS for ch in stripped)
    operators = sum(ch in "+-*/^_{}<>" for ch in stripped)
    total = max(len(stripped), 1)

    if any(hint in font.lower() for font in fonts for hint in MATH_FONT_HINTS):
        alnum = (letters + digits) / total
        if alnum < 0.9 and (math_symbols + operators) >= 1:
            return True
    density = (math_symbols + operators) / total
    if math_symbols >= 2 and density > 0.08:
        return True
    if operators >= 3 and density > 0.18 and italic_ratio > 0.3:
        return True
    return False


def make_block(
    *,
    block_type: str,
    text: str,
    page: int | None,
    level: int = 0,
    fonts: set[str] | None = None,
    cells: list[list[str]] | None = None,
    note: str = "",
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": block_type,
        "level": level,
        "page": page,
        "text": normalize_text(text),
        "note": note,
    }
    if cells is not None:
        block["cells"] = cells
    return block


def attach_segments(blocks: list[dict[str, Any]]) -> None:
    """为每个块生成稳定的 segment id，用于双栏对齐与选中联动。"""
    for seq, block in enumerate(blocks):
        block["seq"] = seq
        if block["type"] == "table":
            grid: list[list[dict[str, str]]] = []
            for row_index, row in enumerate(block.get("cells") or []):
                line: list[dict[str, str]] = []
                for col_index, cell in enumerate(row):
                    text = normalize_text(str(cell))
                    line.append({"sid": f"b{seq}t{row_index}_{col_index}", "src": text})
                grid.append(line)
            block["cells"] = grid
            continue
        text = normalize_text(block.get("text", ""))
        block["text"] = text
        if block["type"] == "formula":
            block["segments"] = [{"sid": f"b{seq}s0", "src": text}]
            continue
        sentences = split_sentences(text) if block["type"] != "reference" else [text]
        if not sentences:
            sentences = [text] if text else []
        block["segments"] = [
            {"sid": f"b{seq}s{index}", "src": sentence}
            for index, sentence in enumerate(sentences)
            if sentence.strip()
        ]


def build_toc(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    toc: list[dict[str, Any]] = []
    for block in blocks:
        if block["type"] in ("heading", "title"):
            toc.append(
                {
                    "seq": block["seq"],
                    "text": block["text"][:120],
                    "level": block.get("level") or 1,
                    "page": block.get("page"),
                    "type": block["type"],
                }
            )
    return toc