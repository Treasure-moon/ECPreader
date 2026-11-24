"""AI 阅读助手端到端检查：接口层 + 浏览器层，全部跑在临时数据目录里。

用一个假的 OpenAI 兼容服务当「AI」，重点验证：
1. 没配置接口时不显示入口，直接请求也只会返回可读的错误；
2. 配置之后能正常流式对话，请求里带着论文正文与软件资料；
3. 请求里绝不出现用户填的 API Key / 邮箱（包括用户自己把 Key 贴进对话）。

    .venv\\Scripts\\python.exe tests\\chat_check.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.mkdtemp(prefix="ecp-chat-")
os.environ["ECP_DATA_DIR"] = TEMP_DIR
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# 版本检查会去「项目仓库」问最新版；测试里指向本机假接口，绝不联网。
RELEASE_HITS: list[str] = []
LATEST_TAG = "v1.6.1"

class MockReleases(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        RELEASE_HITS.append(self.path)
        body = json.dumps({
            "tag_name": LATEST_TAG,
            "name": "ECPreader 1.6.1",
            "published_at": "2026-12-01T08:00:00Z",
            "body": "## 1.6.1 " + "- 修了目录里混进页眉的问题 " + "- 阅读助手可以提示更新",
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_releases() -> str:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = ThreadingHTTPServer(("127.0.0.1", port), MockReleases)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/releases/latest"


os.environ["ECP_RELEASES_API"] = _start_releases()

import httpx  # noqa: E402
import pymupdf  # noqa: E402
import uvicorn  # noqa: E402

from app import chat, config  # noqa: E402
from app.main import TOKEN, create_app  # noqa: E402

API_KEY = "sk-chat-check-SECRET-9f3a71c2"
USER_EMAIL = "reader-private@example.com"
FAILURES: list[str] = []
RECORDED: list[dict] = []
REPLY_PIECES = ["这篇论文", "研究点云", "配准。"]


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}".strip())


# --------------------------------------------------------------- 假的 AI 接口
class MockAIChat(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args) -> None:  # 安静点
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            payload = {}
        RECORDED.append({"path": self.path, "payload": payload,
                        "authorization": self.headers.get("authorization") or ""})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in REPLY_PIECES:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Synthetic Chat Paper", fontsize=20, fontname="hebo")
    page.insert_text((60, 104), "Alice Zhang a, Bob Li a,b,*", fontsize=10)
    page.insert_text((60, 150), "1. Introduction", fontsize=13, fontname="hebo")
    page.insert_text((60, 180), "The paper studies point cloud registration with descriptors.", fontsize=10)
    page.insert_text((60, 210), "Our method improves the correspondence accuracy by 12%.", fontsize=10)
    doc.save(str(path))
    doc.close()


def build_long_pdf(path: Path, pages: int = 12) -> None:
    """12 页、正文远超默认上限的假论文，用来验证「整篇模式」。"""
    doc = pymupdf.open()
    filler = ("Point cloud registration with learned descriptors is evaluated on several "
              "benchmark datasets with multiple baselines and ablations. ") * 26
    for page_no in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 70), f"{page_no}. Section {page_no}", fontsize=14, fontname="hebo")
        marker = "page %d marker" % page_no
        page.insert_textbox(pymupdf.Rect(60, 90, 535, 800), marker + "\n" + filler,
                            fontsize=9)
    doc.save(str(path))
    doc.close()


def sse_payloads(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    return events


def main() -> int:
    mock_port = free_port()
    mock = ThreadingHTTPServer(("127.0.0.1", mock_port), MockAIChat)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    api_port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=api_port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{api_port}"
    headers = {"X-ECP-Token": TOKEN}

    for _ in range(100):
        try:
            if httpx.get(f"{base}/api/health", headers=headers, timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        print("服务没有启动")
        return 1

    try:
        print("长论文的上下文范围")
        blocks = []
        seq = 0
        for page in range(1, 13):
            seq += 1
            blocks.append({"seq": seq, "page": page, "type": "heading",
                           "level": 1, "text": f"{page}. Section {page}"})
            for _ in range(6):
                seq += 1
                blocks.append({"seq": seq, "page": page, "type": "paragraph",
                               "text": f"page {page} " + "正文" * 300})
        long_doc = {"title": "Long Paper", "blocks": blocks}
        ctx = chat.build_context(long_doc, focus_page=9)
        check("长论文判定为截断", ctx["truncated"] is True)
        check("算出截断页码", 0 < ctx["cut_page"] < 12, str(ctx["cut_page"]))
        check("目录覆盖全文", "12. Section 12" in ctx["outline"], ctx["outline"][-80:])
        check("范围说明写明缺哪些页", f"第 {ctx['cut_page'] + 1}–12 页" in ctx["scope"], ctx["scope"])
        check("带上用户当前页正文", "page 9" in ctx["focus"], ctx["focus"][:80])
        check("当前页有标签", ctx["focus_label"] == "第 9 页附近", ctx["focus_label"])
        short = chat.build_context({"title": "Short", "blocks": blocks[:4]})
        check("短论文不截断", short["truncated"] is False and short["scope"].startswith("整篇"),
              short["scope"])

        print("上下文预算设置")
        check("默认预算 24000", chat.context_budget({}) == 24000, str(chat.context_budget({})))
        check("自定义预算生效", chat.context_budget({"chat_budget": 96000}) == 96000)
        check("预算低于下限被抬高", chat.context_budget({"chat_budget": 100}) == chat.MIN_BUDGET)
        check("预算高于上限被压低", chat.context_budget({"chat_budget": 999999}) == chat.HARD_BUDGET)
        check(
            "总结全文开关关闭时仍按上限",
            chat.context_budget({"chat_budget": 24000, "chat_full_on_summary": False}, full=True) == 24000,
        )
        check(
            "总结全文开关打开时发整篇",
            chat.context_budget({"chat_budget": 24000, "chat_full_on_summary": True}, full=True) is None,
        )
        check(
            "普通提问不受整篇开关影响",
            chat.context_budget({"chat_budget": 24000, "chat_full_on_summary": True}) == 24000,
        )
        whole_ctx = chat.build_context(long_doc, budget=None)
        check("整篇模式不截断", whole_ctx["truncated"] is False and not whole_ctx["outline"])
        check("整篇模式说明写整篇", "整篇 12 页" in whole_ctx["scope"], whole_ctx["scope"])

        print("未配置 AI 接口时")
        status = httpx.get(f"{base}/api/settings", headers=headers).json()["chat"]
        check("入口保持关闭", status["enabled"] is False, json.dumps(status, ensure_ascii=False))

        pdf = Path(TEMP_DIR) / "synthetic.pdf"
        build_pdf(pdf)
        with open(pdf, "rb") as handle:
            upload = httpx.post(
                f"{base}/api/documents",
                files={"file": ("synthetic.pdf", handle.read(), "application/pdf")},
                headers=headers,
                timeout=60,
            ).json()
        doc_id = upload["document"]["id"]
        doc_title = upload["document"]["title"]
        check("文档已解析", bool(doc_id) and "synthetic" in doc_title.lower(), doc_title)

        early = httpx.post(
            f"{base}/api/documents/{doc_id}/chat",
            json={"messages": [{"role": "user", "content": "总结一下"}]},
            headers=headers,
            timeout=30,
        )
        events = sse_payloads(early.text)
        check(
            "未配置时给出可操作的提示",
            any(event.get("error", {}).get("code") == "config_missing" for event in events),
            early.text[:200],
        )

        print("版本更新检查")
        info = httpx.post(f"{base}/api/updates/check", headers=headers, timeout=30).json()
        check("查到最新版本", info.get("latest") == "1.6.1", json.dumps(info, ensure_ascii=False)[:300])
        check("判断出有新版", info.get("newer") is True, str(info.get("newer")))
        check("带上发布说明", "修了目录" in (info.get("notes") or ""), (info.get("notes") or "")[:120])
        check("请求到了假仓库", len(RELEASE_HITS) >= 1, str(len(RELEASE_HITS)))
        live = httpx.get(f"{base}/api/updates", headers=headers, timeout=30).json()
        check("状态接口默认开启检查", live.get("enabled") is True, json.dumps(live, ensure_ascii=False)[:200])

        print("配置 AI 接口")
        httpx.put(
            f"{base}/api/settings",
            json={"settings": {
                "engine_mode": "ai",
                "ai_provider": "custom",
                "ai_base_url": f"http://127.0.0.1:{mock_port}/v1",
                "ai_model": "mock-chat",
                "free_email": USER_EMAIL,
            }},
            headers=headers,
        ).raise_for_status()
        httpx.post(
            f"{base}/api/settings/credentials",
            json={"provider": "custom", "api_key": API_KEY},
            headers=headers,
        ).raise_for_status()
        status = httpx.get(f"{base}/api/settings", headers=headers).json()["chat"]
        check("配置后入口打开", status["enabled"] is True, json.dumps(status, ensure_ascii=False))
        check("显示模型名", status.get("model") == "mock-chat", str(status.get("model")))

        print("对话（流式）")
        leaked_question = f"顺便帮我看看这把钥匙 {API_KEY} 和邮箱 {USER_EMAIL} 有没有存错"
        reply = httpx.post(
            f"{base}/api/documents/{doc_id}/chat",
            json={"messages": [
                {"role": "user", "content": "这篇论文做了什么？"},
                {"role": "assistant", "content": "它研究点云配准。"},
                {"role": "user", "content": leaked_question},
            ]},
            headers=headers,
            timeout=60,
        )
        events = sse_payloads(reply.text)
        answer = "".join(event.get("delta", "") for event in events)
        check("流式回答拼接正确", answer == "".join(REPLY_PIECES), repr(answer))
        check("先发 start 事件", any(event.get("start") for event in events), str(events[:1]))
        check("最后 done", events and events[-1].get("done") is True, str(events[-1:]))

        sent = RECORDED[-1]["payload"]
        system_text = sent["messages"][0]["content"]
        user_text = json.dumps(sent["messages"][-1], ensure_ascii=False)
        check("请求带上了论文正文", "point cloud registration with descriptors" in system_text)
        check("请求带上了软件资料", "ECPreader" in system_text and "github.com/Treasure-moon/ECPreader" in system_text)
        # 用户反馈过：AI 不知道反馈邮箱。资料必须自带作者邮箱、版本号和 README 摘录。
        check("资料里有反馈邮箱", "treasure_moon@163.com" in system_text, system_text[:200])
        check("资料里有当前版本号", config.APP_VERSION in system_text, config.APP_VERSION)
        check("资料里点明版本差异", "版本变化" in system_text and "开发版" in system_text)
        check(
            "资料里讲清当前版与已发布版的关系",
            "安装包最新发布" in system_text or "最近发布安装包" in system_text,
            system_text[:200],
        )
        check(
            "资料里说明本地快照不会自己更新",
            "打包进程序的这份资料本身不会更新" in system_text and "releases" in system_text.lower(),
            system_text[:200],
        )
        check(
            "资料里带上联网查到的最新版",
            "联网查到的最新发布" in system_text and "1.6.1" in system_text,
            system_text[:200],
        )
        check(
            "资料里有 README 摘录",
            "两层顶栏" in system_text or "## 功能" in system_text,
            "README 没被读到",
        )
        check("资料里没有本机数据目录", str(TEMP_DIR) not in system_text, "泄漏了数据目录")
        check("提示词要求软件类回答精炼", "6 行以内" in system_text, system_text[:120])
        check("系统提示不算太大", len(system_text) < 40000, f"{len(system_text)} 字符")
        check("只发 system + 对话，没有别的角色", all(
            message["role"] in ("system", "user", "assistant") for message in sent["messages"]
        ))
        check("对话历史按顺序带过去", sent["messages"][1]["role"] == "user" and sent["messages"][-1]["role"] == "user")
        check("流式请求开关打开", sent.get("stream") is True)

        whole_request = json.dumps(sent, ensure_ascii=False)
        check("API Key 没有进入请求体", API_KEY not in whole_request, "泄漏了 Key")
        check("用户邮箱没有进入请求体", USER_EMAIL not in whole_request, "泄漏了邮箱")
        check("用户自己贴的 Key 也被抹掉", "[已隐藏]" in user_text, user_text[:120])
        check("鉴权只在请求头里", RECORDED[-1]["authorization"] == f"Bearer {API_KEY}")

        print("整篇模式（总结全文）")
        long_pdf = Path(TEMP_DIR) / "long.pdf"
        build_long_pdf(long_pdf)
        with open(long_pdf, "rb") as handle:
            long_upload = httpx.post(
                f"{base}/api/documents",
                files={"file": ("long.pdf", handle.read(), "application/pdf")},
                headers=headers,
                timeout=120,
            ).json()
        long_id = long_upload["document"]["id"]
        check("长论文解析成功", bool(long_id), json.dumps(long_upload, ensure_ascii=False)[:200])

        def ask_long(full: bool) -> str:
            reply = httpx.post(
                f"{base}/api/documents/{long_id}/chat",
                json={"messages": [{"role": "user", "content": "总结全文"}], "full": full, "page": 1},
                headers=headers,
                timeout=60,
            )
            return RECORDED[-1]["payload"]["messages"][0]["content"]

        httpx.put(
            f"{base}/api/settings",
            json={"settings": {"chat_budget": 24000, "chat_full_on_summary": False}},
            headers=headers,
        ).raise_for_status()
        limited = ask_long(True)
        check("开关关闭时仍然截断", "page 12 marker" not in limited, f"{len(limited)} 字符")
        check("截断时给出范围与目录",
              "【上下文范围】" in limited and "没有正文；不要用目录编造内容" in limited,
              limited[:160])

        httpx.put(
            f"{base}/api/settings",
            json={"settings": {"chat_full_on_summary": True}},
            headers=headers,
        ).raise_for_status()
        whole = ask_long(True)
        check("开关打开时发送整篇", "page 12 marker" in whole, f"{len(whole)} 字符")
        check("整篇模式说明写整篇", "整篇 12 页" in whole, whole[:160])
        check("整篇模式不再给截断目录", "没有正文；不要用目录编造内容" not in whole)

        normal = ask_long(False)
        check("普通提问不受整篇开关影响", "page 12 marker" not in normal, f"{len(normal)} 字符")
        httpx.put(
            f"{base}/api/settings",
            json={"settings": {"chat_full_on_summary": False}},
            headers=headers,
        ).raise_for_status()

        bad = httpx.put(
            f"{base}/api/settings",
            json={"settings": {"chat_budget": 100}},
            headers=headers,
        ).json()
        check("越界的上下文上限被拒绝", bad["settings"].get("chat_budget") == 24000,
              str(bad["settings"].get("chat_budget")))

        print("浏览器里的悬浮窗")
        import asyncio

        asyncio.run(browser_checks(base, doc_id))

        print("清掉凭据后入口消失")
        httpx.post(
            f"{base}/api/settings/credentials",
            json={"provider": "custom", "clear": True},
            headers=headers,
        ).raise_for_status()
        # 指向 127.0.0.1 的本地模型不需要 Key，这里换成远程地址再验证。
        httpx.put(
            f"{base}/api/settings",
            json={"settings": {"ai_base_url": "https://api.example.com/v1"}},
            headers=headers,
        ).raise_for_status()
        status = httpx.get(f"{base}/api/settings", headers=headers).json()["chat"]
        check("关闭后不可用", status["enabled"] is False, json.dumps(status, ensure_ascii=False))
        check("关闭后给出原因", bool(status.get("reason")), str(status.get("reason")))

        print("关掉版本更新提示后不再联网")
        before = len(RELEASE_HITS)
        httpx.put(
            f"{base}/api/settings",
            json={"settings": {"update_check": False}},
            headers=headers,
        ).raise_for_status()
        off = httpx.post(f"{base}/api/updates/check", headers=headers, timeout=30).json()
        check("开关状态同步到接口", off.get("enabled") is False, json.dumps(off, ensure_ascii=False)[:160])
        check("关掉后没有新请求", len(RELEASE_HITS) == before, f"{before} → {len(RELEASE_HITS)}")
        facts = httpx.get(f"{base}/api/settings", headers=headers).json()
        check("设置里带回开关", facts["settings"].get("update_check") is False,
              str(facts["settings"].get("update_check")))
    finally:
        server.should_exit = True
        mock.shutdown()

    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败")
        for item in FAILURES:
            print(" -", item)
        return 1
    print("\nAI 助手检查全部通过")
    return 0


async def browser_checks(base: str, doc_id: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda err: errors.append(str(err)))
        await page.goto(f"{base}/", wait_until="networkidle")
        await page.wait_for_selector("#recentList .recent-item", timeout=20000)
        await page.locator("#recentList .recent-item").first.click()
        await page.wait_for_selector(".page-pair", timeout=30000)
        await page.wait_for_timeout(600)

        check("阅读页出现 AI 按钮", await page.locator("#btnChat").is_visible())
        check("助手面板默认关闭", await page.locator("#chatPanel").is_hidden())
        await page.locator("#btnChat").click()
        await page.wait_for_timeout(250)
        check("点击后打开悬浮窗", await page.locator("#chatPanel").is_visible())
        disclaimer = (await page.locator(".chat-disclaimer").inner_text()).strip()
        check("界面有免责声明", "费用、错误、产物" in disclaimer and "API Key" in disclaimer, disclaimer)
        check("空状态给出快捷提问", await page.locator(".chat-chip").first.is_visible())
        check("总结全文按钮标了整篇请求",
              await page.locator('.chat-chip[data-full="1"]').count() == 1)
        update_line = await page.locator(".chat-update").count()
        update_text = ""
        if update_line:
            update_text = (await page.locator(".chat-update").inner_text()).strip()
        check("面板提示有新版", update_line == 1 and "1.6.1" in update_text, update_text)

        await page.locator(".chat-chip", has_text="总结全文").click()
        await page.wait_for_selector(".chat-msg.assistant .chat-text", timeout=20000)
        await page.wait_for_timeout(900)
        answer = (await page.locator(".chat-msg.assistant .chat-text").last.inner_text()).strip()
        check("快捷提问拿到流式回答", answer == "".join(REPLY_PIECES), repr(answer))
        check("用户消息也在气泡里", await page.locator(".chat-msg.user").count() >= 1)

        await page.locator("#chatInput").fill("再问一句")
        await page.locator("#chatInput").press("Enter")
        await page.wait_for_timeout(1200)
        check("回车能发送", await page.locator(".chat-msg.user").count() >= 2,
              str(await page.locator(".chat-msg.user").count()))

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        check("Esc 关闭悬浮窗", await page.locator("#chatPanel").is_hidden())

        # 全屏阅读里助手也要能用
        await page.locator("#btnFocus").click()
        await page.wait_for_timeout(500)
        check("进入全屏阅读", await page.evaluate(
            "() => document.body.classList.contains('focus-mode')"))
        check("全屏时助手按钮仍在", await page.locator("#btnChat").is_visible())
        await page.locator("#btnChat").click()
        await page.wait_for_timeout(300)
        check("全屏时能打开悬浮窗", await page.locator("#chatPanel").is_visible())
        await page.locator("#chatInput").fill("全屏时也能问吗")
        await page.locator("#chatInput").press("Enter")
        await page.wait_for_selector(".chat-msg.assistant .chat-text", timeout=20000)
        await page.wait_for_timeout(600)
        check("全屏时能正常对话", await page.locator(".chat-msg.user").count() >= 3,
              str(await page.locator(".chat-msg.user").count()))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)
        check("Esc 只关助手，不退出全屏", await page.locator("#chatPanel").is_hidden()
              and await page.evaluate("() => document.body.classList.contains('focus-mode')"))
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        check("再按 Esc 退出全屏", not await page.evaluate(
            "() => document.body.classList.contains('focus-mode')"))

        # 设置里的上下文上限与整篇开关
        await page.locator("#btnMore").click()
        await page.wait_for_timeout(250)
        await page.locator("#btnSettings").click()
        await page.wait_for_timeout(500)
        budget = await page.locator("#chatBudget").input_value()
        full_switch = await page.locator("#chatFullSummary").is_checked()
        check("设置里有上下文上限", budget == "24000", budget)
        check("整篇开关默认关闭", full_switch is False, str(full_switch))
        check("设置按组分区", await page.locator(".tabpane.active .group-title").count() >= 4,
              str(await page.locator(".tabpane.active .group-title").count()))
        check("AI 模式显示阅读助手分组", await page.locator("#assistantFields").is_visible())
        await page.locator('.mode-card:has-text("传统机翻")').click()
        await page.wait_for_timeout(300)
        check("机翻模式隐藏阅读助手分组", not await page.locator("#assistantFields").is_visible())
        await page.locator('.mode-card:has-text("AI 翻译")').click()
        await page.wait_for_timeout(300)
        check("切回 AI 模式又显示", await page.locator("#assistantFields").is_visible())
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(250)

        check("页面没有 JS 报错", not errors, "; ".join(errors[:2]))
        await browser.close()


if __name__ == "__main__":
    raise SystemExit(main())