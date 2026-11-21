"""单词释义：免费词典 + 机翻词义 + AI 语境意译。"""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import quote

import httpx

from .. import db
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


async def lookup(word: str, sentence: str = "", paragraph: str = "") -> dict:
    cleaned = _clean_word(word)
    if not cleaned:
        raise TranslationError("empty_result", message="没有选中有效的英文单词")
    context_key = hashlib.sha1(
        f"{sentence.strip()}|{paragraph.strip()[:200]}".encode("utf-8")
    ).hexdigest()[:16]
    cached = db.get_dict_cache(cleaned.lower(), context_key)
    if cached:
        return cached

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

    try:
        try:
            info = await definition_task
            phonetic = str(info.get("phonetic") or "")
            definitions = list(info.get("definitions") or [])
        except Exception as exc:
            warnings.append(f"在线词典暂不可用（{type(exc).__name__}）")
        try:
            result = await word_translation_task
            word_zh = (result[0] if result else "").strip()
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