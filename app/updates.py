"""版本更新检查：知道外面发布到哪一版了，才能提醒用户升级。

为什么需要它：打包进 exe 的 README / RELEASES 是发布那一刻的快照，装在用户机器上就
不会再变。AI 助手如果只读本地资料，遇到「我是不是最新版」只能让用户自己去仓库看。
这里改成：向项目仓库问一次最新发布版（只用公开接口、不带任何本机信息），
于是旧版本里的助手也能知道新版本、并主动提醒更新。

出网边界（重要）：
- 只发一个 GET，请求头只有 User-Agent，没有 Cookie、没有装机 ID、没有文档内容；
- 默认开启，可在「翻译设置 → 数据与安全 → 版本更新提示」关掉，关掉后完全不发起请求；
- 不在启动时请求，只在打开阅读助手或用户主动点「检查更新」时才查，结果缓存 6 小时；
- 失败（断网、代理、限流）就安静地当作「不知道」，绝不影响对话。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import httpx

from . import config

SOURCE = os.environ.get(
    "ECP_RELEASES_API",
    "https://api.github.com/repos/Treasure-moon/ECPreader/releases/latest",
)
# 网页版的 latest 会 302 到具体 tag（/releases/tag/v1.6.0），拿它做备用来源：
# GitHub 的匿名 API 每个 IP 每小时只有 60 次，共用出口（校园网、公司网、代理）很容易被限流。
RELEASES_PAGE = os.environ.get(
    "ECP_RELEASES_PAGE",
    "https://github.com/Treasure-moon/ECPreader/releases/latest",
)
TTL = 6 * 3600
TIMEOUT = httpx.Timeout(6.0, connect=4.0, read=6.0)
NOTES_CHARS = 1200

_LOCK = threading.Lock()
_BUSY = threading.Event()
_STATE: dict[str, Any] = {
    "at": 0.0,
    "latest": "",
    "name": "",
    "published": "",
    "notes": "",
    "url": RELEASES_PAGE,
    "error": "",
    "tried": False,
}


def enabled(settings: dict[str, Any] | None = None) -> bool:
    if settings is None:
        from . import db

        settings = db.get_settings()
    return bool(settings.get("update_check", True))


def _parse(text: str) -> tuple[int, ...]:
    digits = []
    for piece in text.strip().lstrip("vV").split(".")[:3]:
        number = "".join(char for char in piece if char.isdigit())
        digits.append(int(number) if number else 0)
    while len(digits) < 3:
        digits.append(0)
    return tuple(digits)


def newer_than(current: str, latest: str) -> bool:
    try:
        return bool(latest) and _parse(latest) > _parse(current)
    except (TypeError, ValueError):
        return False


def _headers() -> dict[str, str]:
    return {
        "User-Agent": f"ECPreader/{config.APP_VERSION} (+https://github.com/Treasure-moon/ECPreader)",
        "Accept": "application/vnd.github+json",
    }


def _fetch_api() -> dict[str, Any]:
    response = httpx.get(
        SOURCE,
        timeout=TIMEOUT,
        follow_redirects=True,
        headers=_headers(),
    )
    response.raise_for_status()
    data = response.json()
    body = str(data.get("body") or "").strip()
    if len(body) > NOTES_CHARS:
        body = body[:NOTES_CHARS].rsplit("\n", 1)[0].rstrip() + "\n……（后面还有）"
    return {
        "latest": str(data.get("tag_name") or "").strip().lstrip("vV"),
        "name": str(data.get("name") or "").strip(),
        "published": str(data.get("published_at") or "")[:10],
        "notes": body,
        "url": RELEASES_PAGE,
    }


def _fetch_page() -> dict[str, Any]:
    """接口被限流时的备用来源：只读 latest 页面的跳转地址，不需要令牌、不占额度。"""
    response = httpx.get(
        RELEASES_PAGE,
        timeout=TIMEOUT,
        follow_redirects=False,
        headers={"User-Agent": _headers()["User-Agent"]},
    )
    location = response.headers.get("location") or ""
    marker = "/releases/tag/"
    if marker not in location:
        raise ValueError("latest 页面没有跳转到具体版本")
    tag = location.split(marker, 1)[1].strip("/").split("/")[0].lstrip("vV")
    if not tag:
        raise ValueError("跳转地址里没有版本号")
    return {"latest": tag, "name": "", "published": "", "notes": "", "url": RELEASES_PAGE}


def _fetch() -> dict[str, Any]:
    try:
        return _fetch_api()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return _fetch_page()


def refresh() -> dict[str, Any]:
    """同步查一次；失败只记错误，不抛异常。"""
    try:
        info = _fetch()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        with _LOCK:
            _STATE.update(at=time.time(), tried=True, error=str(exc)[:200])
        return payload()
    with _LOCK:
        _STATE.update(info, at=time.time(), tried=True, error="")
    return payload()


def start_background(force: bool = False) -> None:
    """过期就在后台查一次；绝不阻塞请求，也绝不并发堆叠。"""
    if not force and time.time() - _STATE["at"] < TTL and _STATE["tried"]:
        return
    if _BUSY.is_set():
        return

    def run() -> None:
        _BUSY.set()
        try:
            refresh()
        finally:
            _BUSY.clear()

    threading.Thread(target=run, name="ecp-updates", daemon=True).start()


def payload() -> dict[str, Any]:
    with _LOCK:
        state = dict(_STATE)
    latest = state["latest"]
    return {
        "enabled": True,
        "current": config.APP_VERSION,
        "latest": latest,
        "newer": newer_than(config.APP_VERSION, latest),
        "relation": relation(config.APP_VERSION, latest),
        "name": state["name"],
        "published": state["published"],
        "notes": state["notes"],
        "url": state["url"],
        "checked": bool(state["at"]),
        "error": state["error"],
    }


def relation(current: str, latest: str) -> str:
    """current 相对 latest：newer（有新版）/ same / source（自己这版比发布版还新）/ unknown。

    查不到（断网、限流、还没发布过）时必须是 unknown——不能把「不知道」说成「你比发布版还新」。
    """
    if not str(latest or "").strip():
        return "unknown"
    try:
        mine, theirs = _parse(current), _parse(latest)
    except (TypeError, ValueError):
        return "unknown"
    if mine < theirs:
        return "newer"
    if mine > theirs:
        return "source"
    return "same"


def facts_block() -> str:
    """给 AI 的资料片段：据实说清三件事——你的版本、发布版、要不要升级。"""
    with _LOCK:
        state = dict(_STATE)
    if not state["latest"]:
        return ""
    current = config.APP_VERSION
    latest = state["latest"]
    stamp = f"（{state['published']}）" if state["published"] else ""
    kind = relation(current, latest)
    if kind == "newer":
        head = (
            f"【联网查到的最新发布】{latest}{stamp}；你现在是 {current}，比发布版旧。"
            f"可以主动提醒用户升级，并给出 {RELEASES_PAGE} 。"
        )
    elif kind == "same":
        head = f"【联网查到的最新发布】{latest}{stamp}，正是你现在运行的版本，已经是最新，不用提醒升级。"
    else:
        head = (
            f"【联网查到的最新发布】{latest}{stamp}；你现在运行的是 {current}，比正式发布版还新，"
            "说明这是开发版／源码版，不要劝用户「升级到旧版」，也不要声称这是最新发布版。"
        )
    notes = state["notes"]
    if notes:
        head += "\n这一版的发布说明（节选）：\n" + notes
    return head