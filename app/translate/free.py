"""免费在线机翻：MyMemory / Google(gtx) / Bing(Edge 令牌)，无需 API Key。"""
from __future__ import annotations

import asyncio
import re
from typing import Iterable

import httpx

from .errors import TranslationError

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
BING_AUTH_URL = "https://edge.microsoft.com/translate/auth"
BING_URL = "https://api-edge.cognitive.microsofttranslator.com/translate"

MYMEMORY_LIMIT = 460
GOOGLE_LIMIT = 1700
BING_LIMIT = 4000

_AUTO_ORDER = ["google", "bing", "mymemory"]
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_LETTER_RE = re.compile(r"[A-Za-z]{2,}")


def needs_translation(text: str) -> bool:
    stripped = (text or "").strip()
    if len(stripped) < 2:
        return False
    if _HAN_RE.search(stripped) and len(_HAN_RE.findall(stripped)) > len(stripped) * 0.3:
        return False
    return bool(_LETTER_RE.search(stripped))


def split_text(text: str, limit: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    pieces = re.split(r"(?<=[.!?;:。；：])\s+", text)
    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        if len(piece) > limit:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            words = piece.split()
            current = ""
            for word in words:
                if len(current) + len(word) + 1 > limit and current:
                    chunks.append(current)
                    current = word
                else:
                    current = (current + " " + word).strip()
            if current:
                chunks.append(current)
            continue
        if not buffer:
            buffer = piece
        elif len(buffer) + len(piece) + 1 <= limit:
            buffer += " " + piece
        else:
            chunks.append(buffer)
            buffer = piece
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c.strip()]


class FreeTranslator:
    """免密钥机翻，带自动服务选择、重试与结果缓存。"""

    def __init__(
        self,
        provider: str = "auto",
        email: str = "",
        timeout: float = 25.0,
        concurrency: int = 3,
        cached_provider: str = "",
    ) -> None:
        self.provider = provider or "auto"
        self.email = (email or "").strip()
        self.timeout = timeout
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._client: httpx.AsyncClient | None = None
        self._resolved: str | None = None
        self._cached = cached_provider if cached_provider in _AUTO_ORDER else ""
        self._cache: dict[str, str] = {}
        self._probe_lock = asyncio.Lock()
        self._bing_token: str = ""

    # ------------------------------------------------------------- lifecycle
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers={"User-Agent": "ECPReader/1.0 (+local academic reader)"},
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self, method: str, url: str, attempts: int = 3, **kwargs
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                response = await self.client().request(method, url, **kwargs)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                    await asyncio.sleep(0.7 * (2**attempt))
                    continue
                return response
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(0.4 * (2**attempt))
                    continue
        if isinstance(last_error, httpx.TimeoutException):
            raise TranslationError("timeout", detail=str(last_error))
        raise TranslationError("network", detail=str(last_error) if last_error else "")

    # --------------------------------------------------------------- resolve
    async def resolved_provider(self) -> str:
        if self._resolved:
            return self._resolved
        async with self._probe_lock:
            if self._resolved:
                return self._resolved
            if self.provider != "auto":
                if self.provider not in _AUTO_ORDER:
                    raise TranslationError("config_missing", message="未知的免费翻译服务")
                self._resolved = self.provider
                return self._resolved

            if self._cached:
                self._resolved = self._cached
                return self._resolved

            async def probe(name: str) -> bool:
                try:
                    await self._translate_with(name, "hello", probe=True)
                    return True
                except Exception:
                    return False

            results = await asyncio.gather(*(probe(name) for name in _AUTO_ORDER))
            for name, ok in zip(_AUTO_ORDER, results):
                if ok:
                    self._resolved = name
                    self._cached = name
                    return name

            self._resolved = "mymemory"
            return self._resolved

    # ------------------------------------------------------------- translate
    async def translate(self, texts: Iterable[str]) -> list[str]:
        items = list(texts)
        provider = await self.resolved_provider()
        try:
            return await self._translate_all(provider, items)
        except TranslationError as exc:
            if self.provider != "auto" or exc.code in ("free_quota", "config_missing"):
                raise
            # 缓存的服务临时不可用：重新探测一次
            self._cached = ""
            self._resolved = None
            fallback = await self.resolved_provider()
            if fallback == provider:
                raise
            return await self._translate_all(fallback, items)

    async def _translate_all(self, provider: str, items: list[str]) -> list[str]:
        async def one(text: str) -> str:
            if not needs_translation(text):
                return text
            key = f"{provider}:{text}"
            if key in self._cache:
                return self._cache[key]
            async with self._sem:
                result = await self._translate_with(provider, text)
                result = await self._ensure_translated(provider, text, result)
            self._cache[key] = result
            return result

        # 同一段里有很多句子时不要一次性打满免费接口：顺序发送，避免首轮
        # 被限流后整段失败（用户看到的就是“第一次翻译失败，手动重译才行”）。
        soft_errors = {
            "rate_limit",
            "timeout",
            "network",
            "upstream_error",
            "bad_response",
            "empty_result",
        }
        results: list[str] = []
        for text in items:
            try:
                results.append(await one(text))
            except TranslationError as exc:
                if exc.code not in soft_errors:
                    raise
                await asyncio.sleep(0.6)
                try:
                    results.append(await one(text))
                except TranslationError as retry_exc:
                    if retry_exc.code not in soft_errors:
                        raise
                    # 单句仍然失败时保留原文，不让整个段落的译文全部丢失。
                    results.append(text)
        return results

    async def _ensure_translated(self, provider: str, text: str, result: str) -> str:
        """机翻原样返回英文时做一次兜底（标题编号、其他服务）。"""
        if not result or result.strip().lower() != text.strip().lower():
            return result
        numbered = re.match(r"^(\d+(?:\.\d+)*\.?)\s+(.+)$", text.strip())
        if numbered and len(text) < 120:
            rest = numbered.group(2)
            try:
                translated_rest = await self._translate_with(provider, rest, probe=True)
            except Exception:
                translated_rest = ""
            if translated_rest and translated_rest.strip().lower() != rest.strip().lower():
                return f"{numbered.group(1)} {translated_rest}"

        if len(text) > 160:
            return result
        others = [name for name in _AUTO_ORDER if name != provider]

        async def attempt(name: str) -> str:
            try:
                return await self._translate_with(name, text, probe=True)
            except Exception:
                return ""

        alternatives = await asyncio.gather(*(attempt(name) for name in others))
        for candidate in alternatives:
            if candidate and candidate.strip().lower() != text.strip().lower():
                return candidate
        return result

    async def _translate_with(self, provider: str, text: str, probe: bool = False) -> str:
        if provider == "google":
            return await self._google(text, probe=probe)
        if provider == "bing":
            return await self._bing(text, probe=probe)
        return await self._mymemory(text, probe=probe)

    # -------------------------------------------------------------- MyMemory
    async def _mymemory(self, text: str, probe: bool = False) -> str:
        timeout = 5.0 if probe else self.timeout
        pieces = split_text(text, MYMEMORY_LIMIT)
        translated_parts: list[str] = []
        for piece in pieces:
            params = {"q": piece, "langpair": "en|zh-CN"}
            if self.email:
                params["de"] = self.email
            response = await self._request(
                "GET", MYMEMORY_URL, params=params, timeout=timeout, attempts=1 if probe else 3
            )
            if response.status_code == 429:
                body = (response.text or "").upper()
                if "USED ALL AVAILABLE FREE TRANSLATIONS" in body or "QUOTA" in body:
                    raise TranslationError(
                        "free_quota", detail="MyMemory 今日匿名额度已用完"
                    )
                raise TranslationError("rate_limit")
            if response.status_code >= 500:
                raise TranslationError("upstream_error", detail=f"HTTP {response.status_code}")
            try:
                data = response.json()
            except Exception as exc:
                raise TranslationError("bad_response", detail=str(exc)) from exc
            if data.get("quotaFinished"):
                raise TranslationError("free_quota")
            status = int(data.get("responseStatus") or 200)
            if status == 403:
                raise TranslationError("free_quota")
            if status == 429:
                raise TranslationError("rate_limit")
            if status != 200:
                raise TranslationError(
                    "upstream_error", detail=str(data.get("responseDetails"))[:200]
                )
            translated = ((data.get("responseData") or {}).get("translatedText") or "").strip()
            if not translated:
                matches = data.get("matches") or []
                if matches:
                    translated = (matches[0].get("translation") or "").strip()
            translated_parts.append(translated or piece)
            if len(pieces) > 1:
                await asyncio.sleep(0.06)
        return " ".join(translated_parts).strip()

    # ---------------------------------------------------------------- Google
    async def _google(self, text: str, probe: bool = False) -> str:
        timeout = 4.0 if probe else self.timeout
        pieces = split_text(text, GOOGLE_LIMIT)
        outputs: list[str] = []
        for piece in pieces:
            params = {
                "client": "gtx",
                "sl": "en",
                "tl": "zh-CN",
                "dt": "t",
                "q": piece,
            }
            response = await self._request(
                "GET", GOOGLE_URL, params=params, timeout=timeout, attempts=1 if probe else 3
            )
            if response.status_code == 429:
                body = (response.text or "").upper()
                if "USED ALL AVAILABLE FREE TRANSLATIONS" in body or "QUOTA" in body:
                    raise TranslationError(
                        "free_quota", detail="Google 免费翻译额度已用完"
                    )
                raise TranslationError("rate_limit")
            if response.status_code >= 500:
                raise TranslationError("upstream_error", detail=f"HTTP {response.status_code}")
            try:
                data = response.json()
            except Exception as exc:
                raise TranslationError("bad_response", detail=str(exc)) from exc
            try:
                segments = data[0] or []
                translated = "".join(
                    segment[0] for segment in segments if segment and segment[0]
                ).strip()
            except Exception as exc:
                raise TranslationError("bad_response", detail=str(exc)) from exc
            if not translated:
                raise TranslationError("empty_result")
            outputs.append(translated)
        return "".join(outputs).strip()

    # ------------------------------------------------------------------ Bing
    async def _bing_token(self, force: bool = False) -> str:
        if self._bing_token and not force:
            return self._bing_token
        response = await self._request("GET", BING_AUTH_URL, timeout=10.0)
        if response.status_code != 200 or not response.text.strip():
            raise TranslationError("network", message="无法获取 Bing 免费翻译令牌")
        self._bing_token = response.text.strip()
        return self._bing_token

    async def _bing(self, text: str, probe: bool = False) -> str:
        timeout = 5.0 if probe else self.timeout
        pieces = split_text(text, BING_LIMIT)[:100]
        for attempt in range(2):
            token = await self._bing_token(force=attempt > 0)
            params = {"api-version": "3.0", "from": "en", "to": "zh-Hans"}
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            response = await self._request(
                "POST",
                BING_URL,
                params=params,
                headers=headers,
                json=[{"Text": piece} for piece in pieces],
                timeout=timeout,
            )
            if response.status_code in (401, 403) and attempt == 0:
                self._bing_token = ""
                continue
            if response.status_code == 429:
                body = (response.text or "").upper()
                if "USED ALL AVAILABLE FREE TRANSLATIONS" in body or "QUOTA" in body:
                    raise TranslationError(
                        "free_quota", detail="Bing 免费翻译额度已用完"
                    )
                raise TranslationError("rate_limit")
            if response.status_code >= 500:
                raise TranslationError("upstream_error", detail=f"HTTP {response.status_code}")
            try:
                data = response.json()
            except Exception as exc:
                raise TranslationError("bad_response", detail=str(exc)) from exc
            parts = [
                (item.get("translations") or [{}])[0].get("text", "")
                for item in data
            ]
            return "".join(parts).strip()
        raise TranslationError("network", message="Bing 免费翻译令牌失效")