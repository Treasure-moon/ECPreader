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
    "acknowledgment",
    "author contributions",
    "data availability",
    "conflict of interest",
    "declaration of competing interest",
    "references",
    "bibliography",
    "appendix",
    "supplementary material",
}

REFERENCE_HEADING_RE = re.compile(
    r"^\s*(references|bibliography|literature cited|参考文献)\s*$", re.IGNORECASE
)

NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\.?\s+[A-Z\u4e00-\u9fff]")
ROMAN_HEADING_RE = re.compile(r"^\s*([IVXLCDM]{1,6})\.\s+(.+)$")
LETTER_HEADING_RE = re.compile(r"^\s*([A-Z])\.\s+(.+)$")
ARABIC_HEADING_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(.+)$")
HEADING_MARKER_RE = re.compile(
    r"^\s*(?:[IVXLCDM]{1,6}\.|\d{1,2}(?:\.\d{1,2}){0,3}\.?|[A-Z]\.)\s+"
)

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
    "=≠≈≤≥±∓×÷−∑∏∫∮√∞∂∇∈∉⊂⊃∪∩∀∃→←↔⇒⇔"
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    "∆∥˜ˆˇ′″"
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


def clean_heading_text(text: str) -> str:
    """去掉 IEEE 论文标题/章节标题后粘连的段首下沉字母（drop cap）。

    例如 ``I. INTRODUCTION E`` 里的 ``E`` 实际是下一段 ``ESTIMATING`` 的首字母，
    被 PDF 文本块并进了标题；它会让目录和译文都多出一个字母。
    """
    stripped = (text or "").strip()
    match = ROMAN_HEADING_RE.match(stripped)
    if not match:
        return stripped
    rest = match.group(2).strip()
    words = rest.split()
    if len(words) < 2 or not rest.isupper():
        return stripped
    tail = words[-1]
    if len(tail) != 1 or not tail.isalpha():
        return stripped
    # 只有当最后一个字母明显是下一段的首字母时才删，避免误伤 “MODEL A”。
    if len(words) >= 3 or words[0].lower() in SECTION_NAMES:
        return f"{match.group(1)}. {' '.join(words[:-1])}"
    return stripped


def _heading_rest_ok(rest: str) -> bool:
    """判断编号后面的文字是否像章节标题，而不是公式/正文残片。"""
    rest = rest.strip()
    if not rest or len(rest) > 140:
        return False
    if rest[-1:] in ".,;:?!":
        return False
    if re.search(r"[=(){}\[\]<>^_|]", rest):
        return False
    letters = sum(ch.isalpha() for ch in rest)
    digits = sum(ch.isdigit() for ch in rest)
    if letters < 2 or digits > letters * 0.6:
        return False
    if not (rest[0].isupper() or "\u4e00" <= rest[0] <= "\u9fff"):
        return False
    return True


def _heading_marker_level(text: str) -> int:
    """根据章节编号返回层级：I. / 1. 为 1，A. / 2.1 为 2。"""
    stripped = text.strip()
    roman = ROMAN_HEADING_RE.match(stripped)
    if roman and (
        len(roman.group(1)) > 1 or roman.group(1) in {"I", "V", "X"}
    ) and _heading_rest_ok(roman.group(2)):
        return 1
    letter = LETTER_HEADING_RE.match(stripped)
    if letter and _heading_rest_ok(letter.group(2)):
        return 2
    arabic = ARABIC_HEADING_RE.match(stripped)
    if arabic and _heading_rest_ok(arabic.group(2)):
        number = arabic.group(1)
        depth = len(number.split("."))
        try:
            first = int(number.split(".")[0])
        except ValueError:
            return 0
        # 排除 “64 J(j−1)” 这类公式残片和年份/页码。
        if first > 99 or not re.search(r"[A-Za-z\u4e00-\u9fff]{2,}", arabic.group(2)):
            return 0
        return min(depth, 3)
    return 0


def _looks_like_heading(
    text: str, size: float, body_size: float, bold_ratio: float
) -> tuple[bool, int]:
    stripped = clean_heading_text(text)
    if not stripped or len(stripped) > 160:
        return False, 0
    if re.search(r"[=(){}\[\]<>^_|−±×÷]", stripped):
        return False, 0
    lowered = stripped.lower().rstrip(".:")
    if lowered in SECTION_NAMES and len(stripped) <= 60:
        return True, 1 if len(stripped.split()) <= 3 else 2
    marker_level = _heading_marker_level(stripped)
    if marker_level:
        return True, marker_level
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
    if not stripped:
        return False
    # PDF 公式块里常混入控制字符（根号、矩阵括号等），这是最可靠的信号。
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", stripped):
        return True
    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", stripped).strip()
    if not clean:
        return True
    if len(clean) > 260:
        return False
    # 只有数字、标点、括号的块（公式编号、根号残片、单个运算符）。
    if re.fullmatch(r"[\s\d.,;:()\[\]{}<>=+\-*/^_|!?′'\"#∥∆∇˜ˆˇ]+", clean):
        return True
    if re.fullmatch(r"[\s.]*\(?\d{1,3}\)?[\s.]*", clean):
        return True

    letters = sum(ch.isalpha() for ch in clean)
    digits = sum(ch.isdigit() for ch in clean)
    math_symbols = sum(ch in MATH_CHARS for ch in clean)
    operators = sum(ch in "+-−*/^_{}<>()[]|" for ch in clean)
    total = max(len(clean), 1)
    words = re.findall(r"[A-Za-z]{2,}", clean)
    lower_words = [word for word in words if word.islower()]
    function_words = re.findall(
        r"\b(?:the|is|are|was|were|of|to|and|in|for|with|that|this|can|be|as|by|"
        r"from|on|at|it|we|our|which|where|when|using|based|denote|denotes|"
        r"represent|represents|obtain|obtained|given|shows|shown|follows|"
        r"respectively|however|therefore|thus|also|has|have|had|not|than)\b",
        clean,
        re.IGNORECASE,
    )
    sentence_like = len(lower_words) >= 2 or bool(function_words)

    # 列表小标题（如 “1) Population Initialization:”）不是公式。
    if re.match(r"^\s*(?:\d+\)|[A-Z]\))\s+[A-Z]", clean):
        return False

    # 带三个以上英文实词、又没有等号/集合符号的块是正文，不是公式
    # （例如 “the overall complexity is O(T × NP × M × N)”）。
    if len(lower_words) >= 3 and not re.search(r"[=∈∉≤≥]", clean):
        return False

    # 标题式短语（标题、DOI 行、图注）即使含连字符/斜杠也不当公式。
    alpha_words = [word for word in re.findall(r"[A-Za-z]+", clean) if len(word) >= 2]
    if (
        len(alpha_words) >= 3
        and sum(word[0].isupper() for word in alpha_words) >= len(alpha_words) * 0.7
        and math_symbols == 0
        and not re.search(r"[=<>∈∉≤≥±×÷∑∫]", clean)
    ):
        return False

    if any(hint in font.lower() for font in fonts for hint in MATH_FONT_HINTS):
        alnum = (letters + digits) / total
        if alnum < 0.9 and (math_symbols + operators) >= 1:
            return True
    density = (math_symbols + operators) / total
    if math_symbols >= 2 and density > 0.06:
        return True
    if len(clean) <= 80 and not sentence_like and (math_symbols + operators) >= 1:
        return True
    if (
        operators >= 2
        and density > 0.12
        and (italic_ratio > 0.25 or not sentence_like)
    ):
        return True
    if len(clean) <= 60 and not sentence_like:
        tokens = re.findall(r"[A-Za-z]+|\d+", clean)
        if tokens and all(len(token) <= 4 for token in tokens):
            alpha_tokens = [token for token in tokens if token.isalpha()]
            # “T, TC”“RC”“Xi,d”这类上下标变量块；全大写多词标题不在此列。
            all_caps_phrase = (
                len(alpha_tokens) >= 2
                and all(token.isupper() for token in alpha_tokens)
                and all(len(token) >= 4 for token in alpha_tokens)
            )
            if not all_caps_phrase and any(
                re.fullmatch(r"[A-Z][A-Za-z]?\d?", token)
                or token.isupper()
                or token.isdigit()
                for token in alpha_tokens
            ):
                return True
        if clean.lower() in {
            "min",
            "max",
            "exp",
            "cos",
            "sin",
            "tan",
            "log",
            "arccos",
            "arcsin",
            "arctan",
            "err",
            "arg",
        }:
            return True
    # 极短的变量残片（t j / b / i / u j −u0）只可能是公式；保留原文。
    if (
        len(clean) <= 20
        and letters >= 1
        and not re.search(r"[a-z]{2,}", clean)
        and not (clean.isupper() and len(clean) >= 4)
        and clean.lower()
        not in {"in", "it", "as", "at", "by", "to", "of", "on", "or", "so", "we", "he", "if", "is", "be", "do", "go", "no", "up", "my", "me", "us"}
    ):
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
    return filter_toc(toc)


# --------------------------------------------------------------- 目录清洗
# PDF 里不少“看起来像标题”的东西会被顺带识别成章节：页眉（每页重复的
# 期刊名、页码 + 刊名）、图注、图片里的坐标轴标签、公式残片、作者行。
# 这些条目在目录里毫无用处，而且会打断章节跳转，所以统一在生成/读取时过滤。

# 图注、表注（含中文「图 / 表」以及 (a)(b) 这样的子图标签）。
TOC_CAPTION_RE = re.compile(
    r"^\s*(?:(?:fig(?:ure)?s?|tab(?:le)?s?|scheme|algorithm|listing|chart)\.?\s*"
    r"(?:[0-9]+[a-zA-Z]?|[IVXivx]+)"
    r"|图\s*[0-9]|表\s*[0-9]|式\s*[0-9]"
    r"|\([a-z]\)|（[a-z]）)",
    re.IGNORECASE,
)

TOC_MATH_CHARS = set(
    "=<>≤≥±×÷∑∫∂√∞≈≠≡∈∉∪∩⊂⊃∝⊥∠°µΩ"
    "αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
)


def normalize_toc_key(text: str) -> str:
    """把随页面变化的数字抹掉，用来识别「每页都重复的那几行」。

    页眉通常带着页码或卷期号（1240 / VOL. 29 / 2025），逐页不同，
    但把数字归一后就是同一串文字。
    """
    lowered = (text or "").lower()
    lowered = re.sub(r"\d+", "#", lowered)
    lowered = re.sub(r"[^0-9a-z#\u4e00-\u9fff]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip(" #")


def loose_toc_key(text: str) -> str:
    """比 normalize_toc_key 更宽：连数字本身也去掉。

    有的期刊把页码放在行首、有的放在行尾（“1240 IEEE TRANSACTIONS …” 对
    “IEEE TRANSACTIONS … 1239”），只把数字换成 # 仍会得到不同的串，
    去掉数字后才能认出是同一行页眉。
    """
    key = re.sub(r"#+", " ", normalize_toc_key(text))
    return re.sub(r"\s+", " ", key).strip()


def _toc_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z]{2,}", text)


BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s+(\S.*)$")


def looks_like_tick_label(text: str) -> bool:
    """“14 MODELS”“1 N” 这类图片刻度/图例：编号后面没有点，只跟一个词。

    真章节标题几乎都带点（``A. Framework`` / ``1. Introduction``）或者跟了
    一整串词，所以这个判据只针对「裸数字 + 单个词」。
    """
    match = BARE_NUMBER_RE.match(text or "")
    if not match:
        return False
    rest = match.group(2).strip()
    if len(re.findall(r"[A-Za-z]{2,}", rest)) >= 2:
        return False
    return rest.lower().rstrip(".:") not in SECTION_NAMES


def toc_entry_ok(text: str, *, level: int = 1) -> bool:
    """判断一条目录项是不是真正的章节标题。"""
    stripped = (text or "").strip()
    # 中文标题可以很短（引言 / 方法 / 结论），英文标题至少 3 个字符。
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", stripped))
    if len(stripped) < (2 if has_cjk else 3) or len(stripped) > 160:
        return False
    if TOC_CAPTION_RE.match(stripped):
        return False
    # 公式残片：等号、集合符号、希腊字母、上下标符号。
    if any(ch in TOC_MATH_CHARS for ch in stripped):
        return False
    # 作者行：一堆逗号分隔的姓名 + 单位角标。
    if stripped.count(",") >= 4:
        return False
    if re.search(r"[\[\]{}^_|]", stripped):
        return False
    words = _toc_words(stripped)
    if not words:
        return bool(re.search(r"[\u4e00-\u9fff]", stripped))
    # 整句（标题不会以句号结尾还带一长串词）。
    if stripped.endswith(".") and len(words) > 6:
        return False
    if looks_like_tick_label(stripped):
        return False
    if _heading_marker_level(stripped):
        return True
    if len(words) < 2:
        # 没有编号的单字标题：只认常见章节名（REFERENCES 之类）。
        return stripped.lower().rstrip(".:") in SECTION_NAMES
    return True


def filter_toc(toc: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """过滤目录里的页眉、图注、公式残片、作者行与重复条目。"""
    entries = [entry for entry in (toc or []) if isinstance(entry, dict)]
    if not entries:
        return []
    # 1) 论文标题之前的条目（期刊名、卷期、DOI 行）不是章节。
    title_index = next(
        (index for index, entry in enumerate(entries) if entry.get("type") == "title"),
        None,
    )
    start = 0 if title_index is None else title_index
    # 2) 出现在 3 个以上不同页面 = 页眉（页码/卷期逐页不同，去掉数字后应一致）。
    pages_by_key: dict[str, set] = {}
    for entry in entries:
        key = loose_toc_key(entry.get("text") or "")
        if len(key) >= 8:
            pages_by_key.setdefault(key, set()).add(entry.get("page"))
    running_headers = {key for key, pages in pages_by_key.items() if len(pages) >= 3}

    output: list[dict[str, Any]] = []
    seen_seq: set = set()
    for index, entry in enumerate(entries):
        text = (entry.get("text") or "").strip()
        if entry.get("type") == "header":
            continue
        if index < start and entry.get("type") != "title":
            continue
        if loose_toc_key(text) in running_headers:
            continue
        if entry.get("seq") in seen_seq:
            continue
        if not toc_entry_ok(text, level=entry.get("level") or 1):
            continue
        seen_seq.add(entry.get("seq"))
        output.append(entry)
    return output