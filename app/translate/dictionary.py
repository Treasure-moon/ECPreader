"""单词释义：免费词典 + 机翻词义 + AI 语境意译。"""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import quote

import httpx

from .. import db
from ..vocab import clean_sense, split_senses, tidy
from .errors import TranslationError, error_payload
from .pipeline import build_ai, build_free

DICT_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"


def _clean_word(raw: str) -> str:
    word = (raw or "").strip()
    word = re.sub(r"^[^\w'-]+|[^\w'-]+$", "", word)
    return word


async def _definitions(word: str) -> dict[str, object]:
    url = DICT_URL.format(word=quote(word.lower()))
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0)) as client:
        response = await client.get(url, headers={"User-Agent": "ECPReader/1.0"})
        if response.status_code == 404:
            return {"phonetic": "", "definitions": []}
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        data = response.json()
    if not isinstance(data, list) or not data:
        return {"phonetic": "", "definitions": []}
    first = data[0]
    phonetic = first.get("phonetic") or ""
    if not phonetic:
        for item in first.get("phonetics") or []:
            if item.get("text"):
                phonetic = item["text"]
                break
    definitions: list[dict[str, str]] = []
    for meaning in (first.get("meanings") or [])[:4]:
        part = meaning.get("partOfSpeech") or ""
        for entry in (meaning.get("definitions") or [])[:2]:
            text = (entry.get("definition") or "").strip()
            if text:
                definitions.append({"pos": part, "def": text})
    return {"phonetic": phonetic, "definitions": definitions[:6]}


def _senses(word_zh: str, ai_senses: list[str], base_sense: str, word: str) -> list[str]:
    """常见直译列表：AI 给的优先，拿不到就拆机翻结果（「银行，河岸」→ 银行/河岸）。"""
    key = word.strip().lower()
    cleaned = [clean_sense(item) for item in ai_senses]
    merged = [
        item
        for item in dict.fromkeys([*cleaned, *split_senses(word_zh)])
        if item and item.strip().lower() != key
    ]
    merged = merged[:6]
    base_sense = clean_sense(base_sense)
    if base_sense and base_sense not in merged:
        merged.append(base_sense)
    if not merged:
        merged = [word]
    return merged


def _with_senses(payload: dict) -> dict:
    """老缓存里没有 senses 字段，读出来时补上，不需要重新联网。

    早期版本把机翻的 `<g id="1">` 标记一起缓存了，这里统一洗一遍。
    """
    payload["word_zh"] = tidy(payload.get("word_zh") or "")
    if payload.get("context_meaning"):
        payload["context_meaning"] = tidy(payload["context_meaning"])
    if not payload.get("senses"):
        word = str(payload.get("word") or "")
        payload["senses"] = _senses(
            str(payload.get("word_zh") or ""), [], str(payload.get("base_sense") or ""), word
        )
    else:
        payload["senses"] = [clean_sense(item) for item in payload["senses"]]
        payload["senses"] = [item for item in dict.fromkeys(payload["senses"]) if item]
    payload.setdefault("base_sense", "")
    return payload


async def lookup(word: str, sentence: str = "", paragraph: str = "") -> dict:
    cleaned = _clean_word(word)
    if not cleaned:
        raise TranslationError("empty_result", message="没有选中有效的英文单词")
    context_key = hashlib.sha1(
        f"{sentence.strip()}|{paragraph.strip()[:200]}".encode("utf-8")
    ).hexdigest()[:16]
    cached = db.get_dict_cache(cleaned.lower(), context_key)
    if cached:
        return _with_senses(dict(cached))

    warnings: list[str] = []
    settings = db.get_settings()
    free = build_free(settings)
    ai = None
    try:
        ai = build_ai(settings)
    except TranslationError:
        ai = None
    except Exception:
        ai = None

    definition_task = asyncio.create_task(_definitions(cleaned))
    word_translation_task = asyncio.create_task(free.translate([cleaned]))
    sentence_task = (
        asyncio.create_task(free.translate([sentence])) if sentence.strip() else None
    )
    ai_task = (
        asyncio.create_task(ai.context_gloss(cleaned, sentence, paragraph))
        if ai is not None and sentence.strip()
        else None
    )

    phonetic = ""
    definitions: list[dict[str, str]] = []
    word_zh = ""
    sentence_zh = ""
    context_meaning = ""
    pos = ""
    engine = "free"
    ai_senses: list[str] = []
    base_sense = ""

    try:
        try:
            info = await definition_task
            phonetic = str(info.get("phonetic") or "")
            definitions = list(info.get("definitions") or [])
        except Exception as exc:
            warnings.append(f"在线词典暂不可用（{type(exc).__name__}）")
        try:
            result = await word_translation_task
            # 机翻会给词条套 <g id="1">…</g>，直接存下来会变成生词本里的乱码
            word_zh = tidy(result[0] if result else "")
        except Exception as exc:
            warnings.append(error_payload(exc)["message"])
        if sentence_task is not None:
            try:
                result = await sentence_task
                sentence_zh = (result[0] if result else "").strip()
            except Exception as exc:
                warnings.append(error_payload(exc)["message"])
        if ai_task is not None:
            try:
                gloss = await ai_task
                context_meaning = gloss.get("context_meaning", "")
                pos = gloss.get("pos", "")
                ai_senses = [str(item) for item in (gloss.get("senses") or []) if str(item).strip()]
                base_sense = str(gloss.get("base_sense") or "").strip()
                if gloss.get("sentence_zh"):
                    sentence_zh = gloss["sentence_zh"]
                engine = f"ai:{settings.get('ai_provider')}"
            except Exception as exc:
                warnings.append("AI 语境释义失败：" + error_payload(exc)["message"])
    finally:
        await free.aclose()
        if ai is not None:
            await ai.aclose()

    payload = {
        "word": cleaned,
        "phonetic": phonetic,
        "definitions": definitions,
        "word_zh": word_zh,
        "context_meaning": context_meaning,
        "pos": pos,
        "senses": _senses(word_zh, ai_senses, base_sense, cleaned),
        "base_sense": base_sense,
        "sentence_en": sentence.strip(),
        "sentence_zh": sentence_zh,
        "engine": engine,
        "warnings": warnings,
    }
    try:
        db.save_dict_cache(cleaned.lower(), context_key, payload)
    except Exception:
        pass
    return payload