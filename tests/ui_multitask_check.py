"""前端检查：多个标签页同时翻译时互不打断（翻译接口用桩替代）。"""
from __future__ import annotations

import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("ECP_BASE_URL", "http://127.0.0.1:8791/")
TOTAL = 12


def job_payload(job_id: str, done: int, status: str = "running") -> dict:
    return {
        "job": {
            "id": job_id,
            "doc_id": job_id,
            "title": "stub",
            "mode": "free",
            "engine": "free",
            "model": "",
            "status": status,
            "total": TOTAL,
            "done": done,
            "failed": 0,
            "error": None,
            "updates": [],
            "cursor": 0,
            "started_at": 0,
            "finished_at": None,
        }
    }


async def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(("PASS " if condition else "FAIL ") + name + (f" · {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context(viewport={"width": 1500, "height": 1000})
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None)

        polls: dict[str, int] = {}

        async def translate_route(route):
            doc_id = route.request.url.split("/documents/")[1].split("/")[0]
            polls[f"job-{doc_id}"] = 0
            await route.fulfill(json=job_payload(f"job-{doc_id}", 0))

        async def job_route(route):
            job_id = route.request.url.split("/jobs/")[1].split("?")[0]
            polls[job_id] = polls.get(job_id, 0) + 1
            done = min(TOTAL, polls[job_id])
            status = "done" if done >= TOTAL else "running"
            await route.fulfill(json=job_payload(job_id, done, status))

        await page.route("**/api/documents/*/translate", translate_route)
        await page.route("**/api/jobs/*", job_route)

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_selector("#recentList .recent-item", timeout=20000)
        items = page.locator("#recentList .recent-item")
        count = await items.count()
        check("至少有两篇论文可供并行", count >= 2, f"{count} 篇")
        if count < 2:
            await browser.close()
            print("失败项：至少有两篇论文可供并行")
            return 1

        # 第一篇开始翻译
        await items.nth(0).click()
        await page.wait_for_selector(".page-pair", timeout=40000)
        await page.wait_for_timeout(700)
        await page.locator("#btnTranslateAll").click()
        await page.wait_for_timeout(500)
        check("翻译进度出现在顶栏", await page.locator("#progressWrap").is_visible())
        check("翻译中标签显示状态点",
              "translating" in (await page.locator(".doc-tab.active .doc-tab-dot").get_attribute("class") or ""))

        # 翻译进行中打开第二篇
        await page.locator("#brandHome").click()
        await page.wait_for_timeout(250)
        await page.locator("#recentList .recent-item").nth(1).click()
        await page.wait_for_selector(".page-pair", timeout=40000)
        await page.wait_for_timeout(900)
        check("切到另一篇时进度条收起", not await page.locator("#progressWrap").is_visible())
        dots = await page.locator(".doc-tab-dot").evaluate_all("els => els.map((el) => el.className)")
        check("后台标签仍在翻译", any("translating" in (d or "") for d in dots), str(dots))

        # 第二篇独立开始翻译
        await page.locator("#btnTranslateAll").click()
        await page.wait_for_timeout(600)
        check("第二篇可同时开始翻译", await page.locator("#progressWrap").is_visible())
        both = await page.locator(".doc-tab-dot").evaluate_all("els => els.map((el) => el.className)")
        check("两篇各自独立翻译中", sum(1 for d in both if "translating" in (d or "")) == 2, str(both))

        # 切回第一篇，进度仍在
        await page.locator(".doc-tab").nth(0).click()
        await page.wait_for_timeout(500)
        check("切回后第一篇进度恢复", await page.locator("#progressWrap").is_visible())
        text = (await page.locator("#progressText").inner_text()).strip()
        check("第一篇进度按自己的任务推进", "/" in text, text)

        # 等第一篇完成，第二篇应仍在翻译
        await page.wait_for_function(
            "() => !document.getElementById('progressWrap').offsetParent",
            timeout=30000,
        )
        dots_after = await page.locator(".doc-tab-dot").evaluate_all("els => els.map((el) => el.className)")
        check("第一篇完成后自身状态为已完成", "done" in (dots_after[0] or ""), str(dots_after))
        check("第二篇仍在后台翻译", "translating" in (dots_after[1] or ""), str(dots_after))
        check("完成后第一篇翻译按钮恢复", await page.locator("#btnTranslateAll").is_visible())

        # 切到第二篇，它的进度接着走
        await page.locator(".doc-tab").nth(1).click()
        await page.wait_for_timeout(500)
        check("第二篇进度独立推进", await page.locator("#progressWrap").is_visible())
        check("两篇文档各自独立", await page.locator(".doc-tab").count() == 2)

        check("无 JS 运行时错误", not errors, "; ".join(errors[:3])[:300])
        await browser.close()

    print()
    if failures:
        print("失败项：" + "、".join(failures))
        return 1
    print("多标签同时翻译检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))