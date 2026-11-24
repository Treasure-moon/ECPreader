"""AI 阅读助手：用用户自己配置的 AI 接口，围绕当前论文对话。

只做两件事：帮用户读懂当前打开的论文，以及回答 ECPreader 本身的问题。

隐私边界（用户明确要求）：
* 发给模型的只有「用户输入 + 论文正文 + 固定的人设」，请求里不包含任何本机
  配置；
* 用户填写的 API Key、邮箱（免费引擎用）在读设置时就被摘出来，并在最后发送
  前再抹一遍（防止用户自己把它贴进对话）；
* 对话只保存在内存里，关掉窗口或切走标签就没了，不落盘、不进历史记录。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from . import assistant_facts, config, db, security, updates
from .translate import pipeline
from .translate.errors import TranslationError

# 论文正文预算：默认约 2.4 万字符（英文≈6k tokens，中文≈2.4 万 tokens），
# 超出部分不发送，并在资料里写明「只包含到第几页」。用户可以在设置里调大，
# 也可以打开「总结全文时发送整篇」——那是给总结/通读用的一次性大请求。
CONTEXT_BUDGET = 24000
MIN_BUDGET = 6000
HARD_BUDGET = 160000   # 再大很多接口会直接拒收，这个上限只做兜底
MAX_MESSAGES = 16
MAX_MESSAGE_CHARS = 4000
MAX_TOTAL_CHARS = 24000
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0, read=300.0, write=30.0)

SYSTEM_PROMPT = """你是 ECPreader 自带的 AI 阅读助手，面向正在读论文的科研人员，只回答两类问题：
1. 当前这篇论文：总结、结构梳理、方法与公式解释、实验与结论、名词与概念解释、写作与延伸问题。
2. ECPreader 这个软件本身：功能、操作、快捷键、设置、版本差异、作者与反馈渠道。

关于论文：以「论文正文」为准，不要编造论文里没有的内容。
论文较长时，正文只包含前面一部分，资料里会写明【上下文范围】（包含到第几页、哪一节为止），
并附上【论文目录】（只有标题和页码，没有正文）与用户当前停留那一页的【你正在看的位置】。
- 目录只能用来指路（第几节叫什么、在第几页），绝不能用目录里的标题去编造这一节讲了什么；
- 范围外的内容就说「这部分正文没有提供」，不要猜节号、不要假装读过，也不要拿导言里的「见第 IV 节」反推内容；
- 用户问的正好在范围外时，建议他停到对应那一页再问（那一页正文会一起发给我），或用文档内查找跳过去。

看得见多少：以【上下文范围】为准。正文不是全文时，第一次谈到论文内容就在句末用一句很短的括号说明，
例如「（我看到的是第 1–4 页）」；之后同一对话里不要每轮重复，除非范围变了。不要拿这行提示凑字数。

只有用户主动问「为什么只能读一部分 / 怎么让它读更多 / 能不能读整篇」时，才引导他去设置：
「⋮ → 翻译设置 → AI 引擎 → 阅读助手上下文上限」可以调大，或者打开「总结全文时发送整篇」。
同时说明取舍：上限每翻一倍，输入 token 与费用大致也翻一倍；换来的是跨章节的一致性、
需要翻十几页才能回答的问题，以及整篇总结不再漏掉后半部分。平时不要主动劝用户改设置。
引用原文要点时用中文解释，术语、符号、公式保持原样。

关于软件：只依据「软件资料」回答，资料里没有就说不知道，并指路「翻译设置」或 Releases 页面。
不要声称自己能读取用户的文件、设置、API Key 或邮箱——你只能看到下面这段论文正文、软件资料和用户输入的文字。

回答软件问题的写法（用户特别要求：精炼、引导式、突出重点）：
- 总长度控制在 6 行以内；第一句就是能照做的结论，之后最多 3 条要点，不要复述功能清单。
- 用「先…再…」的引导句式，一步只讲一个动作，路径写全（例：⋮ → 翻译设置 → AI 引擎 → 填写 API Key）。
- 只答被问到的，不要展开周边功能；能一句话说清就别写两句，不要用空行把回答切成一堆小段。
- 只有真的缺关键信息时才反问，且只问一个问题。
- 提到功能时点明用户当前的版本（现在是 {version} 版）；某个功能是某版本才有的，直接说版本号与差异，别让用户猜。
- 版本相关问题：说明用户当前是 {version} 版；资料里若带「联网查到的最新发布」，就以它为准回答是不是最新，
  旧版本要主动建议升级并给出 Releases 链接；没有联网结果时才说「无法判断，请到 链接 自己看」。
- 问怎么反馈、报 bug、联系作者时：先给邮箱 treasure_moon@163.com，再给仓库 Issues 链接，并提醒附上版本号。

风格：简体中文，先给结论再展开；论文类问题可以用小标题或短列表，公式用行内写法（如 L = 1/N Σ‖q − R p − t‖²）。
不要输出免责声明、不要寒暄，也不要说"作为 AI"之类的废话。"""


def system_prompt() -> str:
    """人设 + 当前版本号（版本取自 config，避免写死在提示词里）。"""
    return SYSTEM_PROMPT.replace("{version}", config.APP_VERSION)


def context_budget(settings: dict[str, Any] | None = None, *, full: bool = False) -> int | None:
    """这次请求给论文正文留多少字符；None 表示整篇都发。

    full=True 表示这次提问是「总结全文 / 通读」这一类，只有用户打开了
    「总结全文时发送整篇」才放过截断——默认关闭，避免一次点错就烧掉大量额度。
    """
    settings = settings if settings is not None else db.get_settings()
    try:
        budget = int(settings.get("chat_budget") or CONTEXT_BUDGET)
    except (TypeError, ValueError):
        budget = CONTEXT_BUDGET
    budget = max(MIN_BUDGET, min(HARD_BUDGET, budget))
    if full and bool(settings.get("chat_full_on_summary")):
        return None
    return budget


def status(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """AI 助手是否可用：配置了 AI 接口（填了 Key，或指向本地模型）才显示入口。"""
    settings = settings or db.get_settings()
    try:
        cfg = pipeline.ai_config(settings)
    except TranslationError as exc:
        return {"enabled": False, "reason": exc.message, "provider": "", "model": ""}
    local = any(host in cfg["base_url"] for host in ("127.0.0.1", "localhost", "0.0.0.0"))
    ready = bool(cfg["api_key"]) or local
    enabled = bool(cfg["base_url"] and cfg["model"] and ready)
    reason = ""
    if not enabled:
        reason = "还没有填写 AI 接口的 API Key" if not ready else "AI 接口地址或模型未填写完"
    return {
        "enabled": enabled,
        "reason": reason,
        "provider": cfg["provider"],
        "model": cfg["model"],
        "kind": cfg["kind"],
    }


def _secrets(settings: dict[str, Any]) -> list[str]:
    """绝不允许出现在请求里的本机配置：所有 API Key + 免费引擎邮箱。"""
    values: list[str] = []
    try:
        keys = security.VAULT.get("api_keys") or {}
    except security.SecurityError:
        keys = {}
    for key in keys.values():
        if isinstance(key, str) and len(key.strip()) >= 6:
            values.append(key.strip())
    email = str(settings.get("free_email") or "").strip()
    if email:
        values.append(email)
    return values


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, "[已隐藏]")
    return text


def _block_text(block: dict[str, Any]) -> str:
    text = (block.get("text") or "").strip()
    if block.get("type") == "table":
        cells = block.get("cells") or []
        rows = [
            " | ".join(str(cell.get("src") or "").strip() for cell in row if isinstance(cell, dict))
            for row in cells
        ]
        text = "\n".join(row for row in rows if row.strip())
    return text


def _render_lines(blocks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for block in blocks:
        text = _block_text(block)
        if not text:
            continue
        kind = block.get("type")
        if kind == "title":
            lines.append(f"【标题】{text}")
        elif kind == "heading":
            lines.append(f"\n## {text}")
        elif kind == "caption":
            lines.append(f"[图注] {text}")
        else:
            lines.append(text)
    return lines


def build_context(
    document: dict[str, Any],
    *,
    budget: int | None = CONTEXT_BUDGET,
    focus_page: int | None = None,
    focus_budget: int = 6000,
) -> dict[str, Any]:
    """把论文压成上下文：正文从头开始装，装不下就把「范围 + 完整目录 + 当前位置」补齐。

    为什么要这样：模型只能看到正文的一部分，如果只丢给它半截文字，
    它会根据导言里的「见第 IV 节」去猜被截掉的内容，听起来像读过，其实是编的。
    所以额外给它三样东西——明确的页码范围、只有标题和页码的完整目录、
    用户当前停留那一页的正文（这样「跳到第 7 页再问」就真的能答）。
    """
    blocks = [
        block
        for block in (document.get("blocks") or [])
        if block.get("type") not in ("header", "reference")
    ]
    total_pages = max((int(block.get("page") or 0) for block in blocks), default=0)
    heading_pages = [
        (int(block.get("page") or 0), (block.get("text") or "").strip())
        for block in blocks
        if block.get("type") == "heading" and (block.get("text") or "").strip()
    ]

    body = "\n".join(_render_lines(blocks)).strip()
    truncated = bool(budget) and len(body) > int(budget)
    cut_page = total_pages
    if truncated:
        body = body[: int(budget)].rsplit("\n", 1)[0].strip()
        used = len(body)
        counted = 0
        for block in blocks:
            counted += len(_block_text(block)) + 1
            if counted > used:
                cut_page = int(block.get("page") or cut_page)
                break

    # 只保留截断点之前的标题，范围说明才不会自相矛盾。
    shown = [item for item in heading_pages if item[0] <= cut_page]
    last_heading = shown[-1][1] if shown else ""

    outline = ""
    if truncated and heading_pages:
        lines = [f"第 {page} 页 · {title}" for page, title in heading_pages[:40]]
        if len(heading_pages) > 40:
            lines.append("……（后面还有标题，这里省略）")
        outline = "\n".join(lines)

    focus = ""
    focus_label = ""
    if truncated and focus_page:
        near = [
            block
            for block in blocks
            if block.get("page") in (focus_page - 1, focus_page)
            and int(block.get("page") or 0) > cut_page
        ]
        text = "\n".join(_render_lines(near)).strip()
        if text:
            if len(text) > focus_budget:
                text = text[:focus_budget].rsplit("\n", 1)[0].strip() + "\n……（这一页后面还有）"
            focus_label = f"第 {focus_page} 页附近"
            focus = text

    scope = ""
    if truncated:
        scope = (
            f"正文只包含前 {cut_page}/{total_pages} 页"
            + (f"（到「{last_heading}」为止）" if last_heading else "")
            + f"，第 {cut_page + 1}–{total_pages} 页没有放进来。"
        )
        if focus:
            scope += f"另外附上了用户正在看的{focus_label}的一段正文。"
    elif total_pages:
        scope = f"整篇 {total_pages} 页正文都在上下文里。"
    return {
        "body": body,
        "truncated": truncated,
        "total_pages": total_pages,
        "cut_page": cut_page,
        "last_heading": last_heading,
        "outline": outline,
        "focus": focus,
        "focus_label": focus_label,
        "scope": scope,
    }


def build_paper_context(
    document: dict[str, Any], *, budget: int | None = CONTEXT_BUDGET
) -> tuple[str, bool]:
    """兼容旧调用：只要正文和是否截断。"""
    info = build_context(document, budget=budget)
    return info["body"], info["truncated"]


def _clean_history(payload: Any) -> list[dict[str, str]]:
    """只接受 user/assistant 的纯文本，长度和轮数都设上限。"""
    if not isinstance(payload, list):
        return []
    cleaned: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        cleaned.append({"role": role, "content": text[:MAX_MESSAGE_CHARS]})
    # 只保留最近若干轮，且最后一条必须是用户提问
    cleaned = cleaned[-MAX_MESSAGES:]
    while cleaned and cleaned[-1]["role"] != "user":
        cleaned.pop()
    while cleaned and cleaned[0]["role"] != "user":
        cleaned.pop(0)
    total = 0
    kept: list[dict[str, str]] = []
    for item in reversed(cleaned):
        total += len(item["content"])
        if total > MAX_TOTAL_CHARS:
            break
        kept.append(item)
    return list(reversed(kept))


def _endpoint(cfg: dict[str, Any]) -> str:
    if cfg["kind"] == "ollama":
        return cfg["base_url"].rstrip("/") + "/api/chat"
    base = cfg["base_url"].rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _status_error(status_code: int, body: str) -> TranslationError:
    detail = redact_error(body)
    if status_code in (401, 403):
        return TranslationError(
            "unauthorized",
            message="AI 接口拒绝了这次请求",
            hint="请在「翻译设置 → AI 引擎」检查 API Key 是否正确、是否过期，以及该账号能否使用所填模型。",
            detail=detail,
        )
    if status_code == 402:
        return TranslationError(
            "quota",
            message="AI 接口额度不足",
            hint="请到服务商控制台充值或更换 Key。AI 助手和翻译共用这一个接口，费用由你自己承担。",
            detail=detail,
        )
    if status_code == 429:
        return TranslationError(
            "rate_limit",
            message="请求过于频繁，被接口限流了",
            hint="等几秒再发；如果一直这样，可以在设置里换一个模型或服务商。",
            detail=detail,
        )
    if status_code == 404:
        return TranslationError("model_not_found", detail=detail)
    if status_code in (400, 422):
        lowered = detail.lower()
        if "context" in lowered or "too long" in lowered or "maximum" in lowered:
            return TranslationError(
                "context_too_long",
                message="这篇论文加上对话超出了模型的上下文长度",
                hint="换用长上下文模型；或者先清空对话，只问一个具体问题。",
                detail=detail,
            )
        return TranslationError("bad_response", detail=detail)
    if status_code >= 500:
        return TranslationError("upstream_error", detail=detail)
    return TranslationError("unknown", detail=detail)


def redact_error(body: str) -> str:
    settings = db.get_settings()
    return redact((body or "")[:400], _secrets(settings))


async def _stream_chat(
    cfg: dict[str, Any], system: str, messages: list[dict[str, str]]
) -> AsyncIterator[str]:
    url = _endpoint(cfg)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if cfg["kind"] != "ollama" and cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, *messages],
        "stream": True,
    }
    if cfg["kind"] == "ollama":
        payload["options"] = {"temperature": 0.6}
    else:
        payload["temperature"] = 0.6

    try:
        async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    raw = await response.aread()
                    raise _status_error(response.status_code, raw.decode("utf-8", "replace"))
                async for line in response.aiter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    data = line[5:].strip() if line.startswith("data:") else line
                    if data in ("[DONE]", ""):
                        if data == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if cfg["kind"] == "ollama":
                        piece = str((chunk.get("message") or {}).get("content") or "")
                        if piece:
                            yield piece
                        if chunk.get("done"):
                            break
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = str(delta.get("content") or "")
                    if not piece:
                        piece = str((choices[0].get("message") or {}).get("content") or "")
                    if piece:
                        yield piece
    except httpx.ConnectError as exc:
        if any(host in cfg["base_url"] for host in ("127.0.0.1", "localhost", "0.0.0.0")):
            raise TranslationError(
                "model_not_running",
                message="连不上本地模型服务",
                hint="先启动 Ollama（ollama serve）或 LM Studio 的 Local Server，确认地址端口后再试。",
                detail=str(exc),
            ) from exc
        raise TranslationError("network", message="连不上 AI 接口", detail=str(exc)) from exc
    except httpx.TimeoutException as exc:
        raise TranslationError(
            "timeout", message="AI 接口响应超时", detail=str(exc)
        ) from exc
    except httpx.HTTPError as exc:
        raise TranslationError("network", message="与 AI 接口的连接中断", detail=str(exc)) from exc


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_events(
    document: dict[str, Any],
    payload_messages: Any,
    payload_page: Any = None,
    payload_full: Any = None,
) -> AsyncIterator[str]:
    """SSE 事件流：逐段返回 AI 回答；出错时给出可读的错误对象。"""
    settings = db.get_settings()
    state = status(settings)
    if not state["enabled"]:
        yield _event({"error": {"code": "config_missing", "message": state["reason"] or "尚未配置 AI 接口",
                                "hint": "打开「翻译设置 → AI 引擎」填写 API Key，或指向本地模型。"}, "done": True})
        return
    history = _clean_history(payload_messages)
    if not history:
        yield _event({"error": {"code": "empty_result", "message": "问题不能为空",
                                "hint": "在输入框里写下问题再发送。"}, "done": True})
        return

    secrets = _secrets(settings)
    focus_page = None
    try:
        candidate = int(payload_page or 0)
        if 1 <= candidate <= 9999:
            focus_page = candidate
    except (TypeError, ValueError):
        focus_page = None
    whole = bool(payload_full)
    budget = context_budget(settings, full=whole)
    ctx = build_context(document, budget=budget, focus_page=focus_page)
    title = (document.get("title") or document.get("filename") or "未命名文档").strip()
    facts = assistant_facts.build_facts()
    # 联网查到的最新版本：让旧版本的助手也知道外面发了什么，必要时提醒升级。
    if updates.enabled(settings):
        live = updates.facts_block()
        if live:
            facts = f"{facts}\n\n{live}"
    parts = [f"{system_prompt()}\n\n=== 软件资料 ===\n{facts}", f"=== 论文正文 ===\n标题：{title}"]
    if ctx["scope"]:
        parts.append(f"【上下文范围】{ctx['scope']}")
    if ctx["outline"]:
        parts.append(
            "【论文目录】（只有标题和页码，没有正文；不要用目录编造内容）\n" + ctx["outline"]
        )
    parts.append(ctx["body"])
    if ctx["focus"]:
        parts.append(
            f"【用户正在看的位置 · {ctx['focus_label']}】（这段是正文，可以据此回答）\n{ctx['focus']}"
        )
    system = redact("\n\n".join(parts), secrets)
    messages = [
        {"role": item["role"], "content": redact(item["content"], secrets)}
        for item in history
    ]
    try:
        cfg = pipeline.ai_config(settings)
    except TranslationError as exc:
        yield _event({"error": exc.to_dict(), "done": True})
        return

    yield _event({
        "start": True,
        "truncated": ctx["truncated"],
        "cut_page": ctx["cut_page"] if ctx["truncated"] else 0,
        "total_pages": ctx["total_pages"],
        "focus_page": focus_page if ctx["focus"] else 0,
        "whole": bool(budget is None),
        "model": cfg["model"],
    })
    produced = False
    try:
        async for piece in _stream_chat(cfg, system, messages):
            produced = True
            yield _event({"delta": piece})
    except TranslationError as exc:
        error = exc.to_dict()
        error["detail"] = redact(error.get("detail") or "", secrets)
        yield _event({"error": error, "done": True})
        return
    except Exception as exc:  # 兜底：任何意外都要让前端把转圈停掉
        yield _event({"error": {"code": "unknown", "message": "AI 助手出错了",
                                "hint": "可以重试一次；如果一直失败，检查接口地址与网络。",
                                "detail": str(exc)[:200]}, "done": True})
        return
    if not produced:
        yield _event({"error": {"code": "empty_result", "message": "AI 没有返回内容",
                                "hint": "换个问法再试，或者换一个模型。"}, "done": True})
        return
    yield _event({"done": True})