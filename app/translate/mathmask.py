"""翻译前保护公式/变量：用占位符替换数学片段，译后再原样还原。

机翻服务不認識论文里的上下标变量，常把它们当成英文单词翻译。比如
``P = {Pi}`` 会被拆成奇怪的词组。这里只在**翻译请求**里使用占位符，
界面上仍然显示完整的原始数学符号。
"""
from __future__ import annotations

import re

# 不在变量保护范围内的常见短英文词，避免把 “The / In / It” 当成变量。
_STOPWORDS = {
    "a",
    "i",
    "an",
    "the",
    "and",
    "but",
    "for",
    "nor",
    "not",
    "are",
    "was",
    "were",
    "has",
    "had",
    "can",
    "may",
    "use",
    "our",
    "its",
    "his",
    "her",
    "all",
    "any",
    "one",
    "two",
    "new",
    "per",
    "via",
    "see",
    "set",
    "let",
    "now",
    "then",
    "thus",
    "also",
    "both",
    "each",
    "such",
    "same",
    "only",
    "more",
    "most",
    "some",
    "many",
    "much",
    "very",
    "well",
    "in",
    "it",
    "as",
    "at",
    "by",
    "to",
    "of",
    "on",
    "or",
    "so",
    "we",
    "he",
    "if",
    "is",
    "be",
    "do",
    "go",
    "no",
    "up",
    "my",
    "me",
    "us",
    "vs",
    "al",
    "dr",
    "st",
    "mr",
    "ms",
    "eq",
    "fig",
    "ref",
    "sec",
    "tab",
    "et",
}

# 只保护数学记号本身，不吞掉整句英文。等式/集合式里的普通单词仍交给
# 翻译服务，避免出现 “The point set ⟦M0⟧.” 这种把谓语也保护掉的情况。
_MATH_SPAN_RE = re.compile(
    r"(?P<sub>\b[A-Za-z]\s*[_^]\s*\{?[A-Za-z0-9]+\}?)"
    r"|(?P<var>\b[A-Z][A-Za-z]?\d?\b)"
    r"|(?P<greek>[α-ωΑ-Ω])"
)


def mask_math(text: str) -> tuple[str, list[str]]:
    """返回 (带占位符的文本, 原数学片段列表)。"""
    tokens: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        if match.lastgroup == "var" and raw.lower() in _STOPWORDS:
            return raw
        token = f"⟦M{len(tokens)}⟧"
        tokens.append(raw)
        return token

    masked = _MATH_SPAN_RE.sub(replace, text or "")
    return masked, tokens


def restore_math(text: str, tokens: list[str]) -> str:
    """把占位符换回原数学片段；容忍服务在占位符里加空格。"""
    restored = text or ""
    if not tokens:
        return restored

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return tokens[index] if 0 <= index < len(tokens) else match.group(0)

    return re.sub(r"⟦\s*M\s*(\d+)\s*⟧", replace, restored)