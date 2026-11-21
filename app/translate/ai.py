"""AI 翻译：OpenAI 兼容接口（DeepSeek / OpenAI / 硅基流动 / 自定义）与本地 Ollama。"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from .errors import TranslationError

SYSTEM_PROMPT = """你是一名专业的学术论文翻译专家，负责把英文科研论文准确翻译为简体中文，读者是科研人员。必须遵守：

1. 准确性第一：忠实、完整地传达原文的信息与逻辑关系，不增删、不演绎、不改变数字、单位、引用编号（如 [12]、Figure 3、Eq. (2)）。
2. 使用学术书面语，通顺自然，避免口语化。长句可以适度拆分，但不得改变语义或因果关系。
3. 术语保持一致：同一术语在全篇使用同一译法，严格遵守用户提供的术语表。
4. 保留原貌：公式、变量符号、代码、URL、DOI、作者姓名、机构名、数据集名、模型名、标准缩写保持英文（首次出现可在括号内补充中文）。
5. 图表标签：Figure/Table/Algorithm 译为 图/表/算法，编号保留；标题类条目简洁准确，不添加原文没有的内容。
6. 每个条目独立翻译，输出条目数必须与输入完全一致，禁止合并或拆分条目。
7. 只输出 JSON 对象，格式为 {"translations":[{"i":0,"zh":"译文"}]}，不要输出任何解释、注释或 Markdown 代码块。
"""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_translation_json(raw: str) -> dict[int, str]:
    text = _strip_code_fence(raw)
    data: Any = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = None
    if data is None:
        raise TranslationError("bad_response", detail="模型没有返回合法 JSON")
    entries = data.get("translations") if isinstance(data, dict) else None
    if entries is None and isinstance(data, dict) and "results" in data:
        entries = data["results"]
    if not isinstance(entries, list):
        raise TranslationError("bad_response", detail="JSON 中缺少 translations 数组")
    output: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        index = entry.get("i", entry.get("index"))
        text = entry.get("zh", entry.get("translation", entry.get("text")))
        if isinstance(index, int) and isinstance(text, str):
            output[index] = text.strip()
    if not output:
        raise TranslationError("bad_response", detail="JSON 中没有可用的译文")
    return output


class AITranslator:
    def __init__(
        self,
        *,
        provider: str = "deepseek",
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        kind: str = "openai",
        temperature: float = 0.2,
        timeout: float = 120.0,
    ) -> None:
        self.provider = provider
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.kind = kind
        self.temperature = temperature
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._json_mode = True

    # ------------------------------------------------------------- lifecycle
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- call
    def _endpoint(self) -> str:
        base = self.base_url
        if self.kind == "ollama":
            return base + "/api/chat"
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    async def _chat(self, messages: list[dict[str, str]], max_tokens: int = 4096) -> str:
        if not self.base_url or not self.model:
            raise TranslationError("config_missing")
        url = self._endpoint()
        headers = {"Content-Type": "application/json"}
        if self.kind != "ollama" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self.kind == "ollama":
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "options": {"temperature": self.temperature},
            }
        else:
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            if self._json_mode:
                payload["response_format"] = {"type": "json_object"}

        for attempt in range(2):
            try:
                response = await self.client().post(url, headers=headers, json=payload)
            except httpx.ConnectError as exc:
                if any(h in self.base_url for h in ("127.0.0.1", "localhost", "0.0.0.0")):
                    raise TranslationError("model_not_running", detail=str(exc)) from exc
                raise TranslationError("network", detail=str(exc)) from exc
            except httpx.TimeoutException as exc:
                raise TranslationError("timeout", detail=str(exc)) from exc
            except httpx.HTTPError as exc:
                raise TranslationError("network", detail=str(exc)) from exc

            if response.status_code == 400 and payload.get("response_format") and attempt == 0:
                # 部分兼容接口不支持 response_format，去掉后重试一次
                payload.pop("response_format", None)
                self._json_mode = False
                continue
            self._raise_for_status(response.status_code, response.text)
            try:
                data = response.json()
            except Exception as exc:
                raise TranslationError("bad_response", detail=str(exc)) from exc

            if self.kind == "ollama":
                content = ((data.get("message") or {}).get("content") or "").strip()
            else:
                choices = data.get("choices") or []
                if not choices:
                    raise TranslationError("bad_response", detail=str(data)[:300])
                content = ((choices[0].get("message") or {}).get("content") or "").strip()
            if not content:
                raise TranslationError("empty_result")
            return content
        raise TranslationError("bad_response")

    def _raise_for_status(self, status: int, body: str) -> None:
        if status < 400:
            return
        detail = (body or "")[:400]
        if status in (401, 403):
            raise TranslationError("unauthorized", detail=detail)
        if status == 402:
            raise TranslationError("quota", detail=detail)
        if status == 429:
            raise TranslationError("rate_limit", detail=detail)
        if status == 404:
            raise TranslationError("model_not_found", detail=detail)
        if status in (400, 422):
            lowered = detail.lower()
            if "context" in lowered or "too long" in lowered or "maximum" in lowered:
                raise TranslationError("context_too_long", detail=detail)
            raise TranslationError("bad_response", detail=detail)
        if status >= 500:
            raise TranslationError("upstream_error", detail=detail)
        raise TranslationError("unknown", detail=detail)

    # ----------------------------------------------------------- translate
    async def translate_items(
        self,
        items: list[dict[str, Any]],
        meta: dict[str, Any] | None = None,
    ) -> dict[int, str]:
        """items: [{"i": idx, "text": str, "kind": "paragraph|heading|caption|cell"}]

        返回 {idx: 译文}；自动按长度分批，每批独立请求。
        """
        meta = meta or {}
        if not items:
            return {}
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        budget = 0
        for item in items:
            length = len(str(item.get("text", ""))) + len(str(item.get("kind", ""))) + 40
            if current and budget + length > 3200:
                batches.append(current)
                current = []
                budget = 0
            current.append(item)
            budget += length
        if current:
            batches.append(current)

        output: dict[int, str] = {}
        for batch in batches:
            output.update(await self._translate_batch(batch, meta))
        return output

    async def _translate_batch(
        self, batch: list[dict[str, Any]], meta: dict[str, Any]
    ) -> dict[int, str]:
        payload = [
            {
                "i": item["i"],
                "kind": item.get("kind", "paragraph"),
                "text": item["text"],
            }
            for item in batch
        ]
        user_message = self._build_user_message(payload, meta)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        raw = await self._chat(messages, max_tokens=min(8192, 600 + len(str(payload)) // 2))
        parsed = parse_translation_json(raw)
        missing = [item["i"] for item in batch if item["i"] not in parsed]
        if missing:
            # 缺失条目单独补译，保证对齐不丢段
            for item in batch:
                if item["i"] not in parsed:
                    single = self._build_user_message(
                        [
                            {
                                "i": item["i"],
                                "kind": item.get("kind", "paragraph"),
                                "text": item["text"],
                            }
                        ],
                        meta,
                    )
                    retry_raw = await self._chat(
                        [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": single},
                        ],
                        max_tokens=2048,
                    )
                    parsed.update(parse_translation_json(retry_raw))
            still_missing = [i for i in missing if i not in parsed]
            if still_missing:
                raise TranslationError(
                    "bad_response", detail=f"模型漏译了 {len(still_missing)} 个条目"
                )
        return parsed

    def _build_user_message(
        self, payload: list[dict[str, Any]], meta: dict[str, Any]
    ) -> str:
        title = meta.get("title") or "（未知）"
        section = meta.get("section") or "（正文）"
        glossary = meta.get("glossary") or "（无）"
        prev_en = (meta.get("prev_en") or "").strip()
        prev_zh = (meta.get("prev_zh") or "").strip()
        lines = [
            f"论文标题：{title}",
            f"当前章节：{section}",
            f"术语表（必须遵守）：{glossary}",
        ]
        if prev_en:
            lines.append(f"上一段原文（仅作上下文参考，不要翻译）：{prev_en[:400]}")
        if prev_zh:
            lines.append(f"上一段译文（保持衔接与术语一致）：{prev_zh[:400]}")
        lines.append("待翻译条目（JSON 数组，按 i 原样返回）：")
        lines.append(json.dumps(payload, ensure_ascii=False))
        lines.append('请输出：{"translations":[{"i":0,"zh":"..."}]}')
        return "\n".join(lines)

    # -------------------------------------------------------------- extras
    async def build_glossary(self, title: str, sample: str) -> dict[str, str]:
        """从标题 + 摘要中抽取核心术语，保证全篇译法统一。"""
        if not sample.strip():
            return {}
        prompt = (
            "请从下面这篇英文论文的标题和摘要中，抽取 8–15 个必须保持全篇译法一致的专业术语"
            "（领域概念、方法名、指标名；不要抽取普通词、作者名、数据集名）。\n"
            f"标题：{title}\n摘要片段：{sample[:1800]}\n"
            '只输出 JSON：{"glossary":[{"en":"term","zh":"中文译名"}]}'
        )
        try:
            raw = await self._chat(
                [
                    {
                        "role": "system",
                        "content": "你是学术术语专家，只输出严格 JSON。",
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=800,
            )
            text = _strip_code_fence(raw)
            match = re.search(r"\{.*\}", text, re.S)
            data = json.loads(match.group(0) if match else text)
            result: dict[str, str] = {}
            for entry in data.get("glossary", []):
                en = str(entry.get("en", "")).strip()
                zh = str(entry.get("zh", "")).strip()
                if en and zh and len(en) < 60:
                    result[en] = zh
            return result
        except Exception:
            return {}

    async def test_connection(self) -> str:
        result = await self.translate_items(
            [{"i": 0, "text": "This paper proposes a new method for document layout analysis.", "kind": "paragraph"}],
            {"title": "connection test"},
        )
        return result.get(0, "")

    async def context_gloss(self, word: str, sentence: str, paragraph: str = "") -> dict[str, str]:
        """结合语境解释单词，并给出整句意译。"""
        prompt = (
            f"单词：{word}\n"
            f"所在句子：{sentence[:600]}\n"
            f"所在段落（可选上下文）：{paragraph[:800]}\n\n"
            "请完成：\n"
            "1) 该词在此语境下的准确中文含义（一个简短词组，不要罗列词典义项）；\n"
            "2) 词性（n./v./adj./adv./phr. 等）；\n"
            "3) 整句的学术化中文意译（通顺自然，保持原意）。\n"
            '只输出 JSON：{"pos":"...","context_meaning":"...","sentence_zh":"..."}'
        )
        raw = await self._chat(
            [
                {"role": "system", "content": "你是学术英语词典与翻译专家，只输出严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
        )
        text = _strip_code_fence(raw)
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0) if match else text)
        return {
            "pos": str(data.get("pos", "")).strip(),
            "context_meaning": str(data.get("context_meaning", "")).strip(),
            "sentence_zh": str(data.get("sentence_zh", "")).strip(),
        }