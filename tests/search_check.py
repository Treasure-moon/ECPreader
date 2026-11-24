"""前端检查：Ctrl+F 文档内查找、原文/译文高亮、上下跳转与关闭。"""
from __future__ import annotations

import asyncio
import os
import sys

from playwright.async_api import async_playwright

BASE = os.environ.get("ECP_BASE_URL", "http://127.0.0.1:8791/")


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
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        async def highlight_texts(name: str) -> list[str]:
            return await page.evaluate(
                """(name) => {
                    const h = CSS.highlights.get(name);
                    return h ? [...h].map((r) => r.toString()) : [];
                }""",
                name,
            )

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_selector("#recentList .recent-item", timeout=20000)
        await page.locator("#recentList .recent-item").first.click()
        await page.wait_for_selector(".page-pair", timeout=40000)
        await page.wait_for_function(
            "() => document.querySelectorAll('.textlayer .w[data-sid]').length > 50",
            timeout=40000,
        )

        # 原版视图：只查半个单词，验证只高亮输入的字母
        await page.keyboard.press("Control+f")
        check("Ctrl+F 打开查找栏", await page.locator("#searchBar").is_visible())
        await page.locator("#searchInput").fill("pro")
        await page.wait_for_timeout(1000)
        count_text = (await page.locator("#searchCount").inner_text()).strip()
        source_ranges = await highlight_texts("ecp-search")
        current_ranges = await highlight_texts("ecp-search-current")
        check("原文查找有命中", bool(source_ranges), f"{count_text} · {len(source_ranges)} 处")
        check("只高亮匹配的字母", bool(source_ranges) and all(x.lower() == "pro" for x in source_ranges),
              "、".join(source_ranges[:5]))
        check("当前命中只高亮匹配的字母",
              bool(current_ranges) and all(x.lower() == "pro" for x in current_ranges))
        check("查找计数显示位置", "/" in count_text and "原文" in count_text, count_text)

        first_count = count_text
        await page.locator("#searchInput").press("Enter")
        await page.wait_for_timeout(250)
        second_count = (await page.locator("#searchCount").inner_text()).strip()
        check("Enter 跳到下一个命中", first_count != second_count, f"{first_count} → {second_count}")

        # 较后页面的命中应自动滚动过去
        await page.locator("#searchInput").fill("CONCLUSION")
        await page.wait_for_timeout(1200)
        jump_count = (await page.locator("#searchCount").inner_text()).strip()
        jump_scroll = await page.evaluate("document.getElementById('paper').scrollTop")
        check("跨页命中可跳转", "无结果" not in jump_count and jump_scroll > 100,
              f"{jump_count} · scrollTop={round(jump_scroll)}")

        # 原版视图：查找中文译文（用当前文档里真实出现多次的词，不写死）
        zh_count = ""
        zh_ranges: list[str] = []
        zh_current: list[str] = []
        term = ""

        await page.keyboard.press("Escape")
        check("Esc 关闭查找栏", not await page.locator("#searchBar").is_visible())
        check(
            "关闭后清除高亮",
            not await page.evaluate(
                "() => Boolean(CSS.highlights.get('ecp-search') || CSS.highlights.get('ecp-search-current'))"
            ),
        )
        await page.locator("#btnSearch").click()
        check("顶部查找按钮也能打开", await page.locator("#searchBar").is_visible())
        await page.locator("#searchClose").click()

        # 逐句视图：查找、当前项与跳转
        await page.locator('#viewSwitch [data-view="flow"]').click()
        await page.wait_for_timeout(500)
        # 从当前文档的译文里挑一个出现最多的两字词，避免绑死某篇论文的用词。
        term = await page.evaluate(
            """() => {
                const tgt = [...document.querySelectorAll('.brow .pane.tgt')]
                    .map((node) => node.innerText).join('\\n');
                const counts = new Map();
                for (const match of tgt.matchAll(/[\\u4e00-\\u9fff]{2}/g)) {
                    counts.set(match[0], (counts.get(match[0]) || 0) + 1);
                }
                const best = [...counts.entries()].filter(([, n]) => n >= 3)
                    .sort((a, b) => b[1] - a[1]);
                return best.length ? best[0][0] : '';
            }"""
        )
        check("能找到可跳转的译文用词", bool(term), term)
        await page.keyboard.press("Control+f")
        await page.locator("#searchInput").fill(term)
        await page.wait_for_timeout(600)
        flow_count = (await page.locator("#searchCount").inner_text()).strip()
        flow_ranges = await highlight_texts("ecp-search")
        flow_current = await highlight_texts("ecp-search-current")
        check("逐句视图查找命中", bool(flow_ranges), f"{flow_count} · {len(flow_ranges)} 处")
        check("逐句视图只高亮匹配文字", bool(flow_ranges) and all(x == term for x in flow_ranges),
              "、".join(flow_ranges[:5]))
        check("逐句视图当前项只高亮匹配文字", bool(flow_current) and all(x == term for x in flow_current))
        before = flow_count
        await page.locator("#searchInput").press("Enter")
        await page.wait_for_timeout(300)
        after = (await page.locator("#searchCount").inner_text()).strip()
        check("逐句视图可跳下一句", before != after, f"{before} → {after}")
        await page.keyboard.press("Escape")

        # 回到原版视图：中文译文的查找与高亮
        await page.locator('#viewSwitch [data-view="layout"]').click()
        await page.wait_for_timeout(600)
        await page.keyboard.press("Control+f")
        await page.locator("#searchInput").fill(term)
        await page.wait_for_timeout(700)
        zh_count = (await page.locator("#searchCount").inner_text()).strip()
        zh_ranges = await highlight_texts("ecp-search")
        zh_current = await highlight_texts("ecp-search-current")
        check("译文查找有命中", bool(zh_ranges), f"{zh_count} · {len(zh_ranges)} 处")
        check("译文只高亮匹配的文字", bool(zh_ranges) and all(x == term for x in zh_ranges),
              "、".join(zh_ranges[:5]))
        check("译文当前命中只高亮匹配文字", bool(zh_current) and all(x == term for x in zh_current))
        check("译文查找标明译文", "译文" in zh_count, zh_count)
        await page.keyboard.press("Escape")

        check("无 JS 运行时错误", not errors, "; ".join(errors[:3])[:200])
        await browser.close()

    print()
    if failures:
        print("失败项：" + "、".join(failures))
        return 1
    print("查找功能检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))