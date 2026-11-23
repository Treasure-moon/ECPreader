"""前端检查：单层顶栏、多标签页、全屏阅读与目录抽屉。"""
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
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None)
        page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_selector("#recentList .recent-item", timeout=20000)

        # 首页：只有一层顶栏，阅读控件收起
        check("初始只有一个首页标签", await page.locator(".doc-tab").count() == 1)
        check("首页标签标题为首页", "首页" in await page.locator(".doc-tab").first.inner_text())
        check("首页隐藏阅读控件", not await page.locator("#btnTranslateAll").is_visible())
        check("首页可见新建标签按钮", await page.locator("#btnNewTab").is_visible())

        # 「⋮」菜单只收纳全局入口，论文相关项在第二层「⋯」里
        await page.locator("#btnMore").click()
        await page.wait_for_timeout(150)
        check("全局菜单可打开", await page.locator("#moreMenu").is_visible())
        for item in ["#btnOpen", "#btnHistory", "#engineSelect", "#btnSettings", "#btnTheme"]:
            check(f"全局菜单包含 {item}", await page.locator(item).is_visible())
        check("全局菜单不含论文操作", not await page.locator("#btnExport").is_visible())
        check("全局菜单不再藏「关于」", not await page.locator("#btnAbout").count())
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        check("Esc 关闭全局菜单", not await page.locator("#moreMenu").is_visible())

        # 「关于」放在外面，一键可开
        check("关于按钮常驻顶栏", await page.locator("#btnAboutTop").is_visible())
        await page.locator("#btnAboutTop").click()
        await page.wait_for_timeout(250)
        check("一键打开关于", await page.locator("#aboutModal").is_visible())
        await page.locator('#aboutModal [data-close="about"]').last.click()
        await page.wait_for_timeout(200)
        check("关于可关闭", not await page.locator("#aboutModal").is_visible())

        # 新建空白标签页，并在其中打开论文
        await page.locator("#btnNewTab").click()
        await page.wait_for_timeout(200)
        check("新建标签页", await page.locator(".doc-tab").count() == 2)
        await page.locator("#recentList .recent-item").first.click()
        await page.wait_for_selector(".page-pair", timeout=40000)
        await page.wait_for_function(
            "() => { const img = document.querySelector('.page-img');"
            " return img && img.complete && img.naturalWidth > 100; }",
            timeout=40000,
        )

        # 两层顶栏：上层是全局栏（品牌/标签/引擎/设置），下层只放阅读控件
        topbar_h = await page.locator("#topbar").evaluate("el => el.getBoundingClientRect().height")
        toolbar_h = await page.locator("#readerToolbar").evaluate("el => el.getBoundingClientRect().height")
        layout = await page.evaluate("""() => {
          const bar = document.getElementById('topbar').getBoundingClientRect();
          const tool = document.getElementById('readerToolbar').getBoundingClientRect();
          const inBar = (id) => document.getElementById(id).getBoundingClientRect();
          const view = inBar('viewSwitch').top >= tool.top - 1 && inBar('viewSwitch').bottom <= tool.bottom + 1;
          const tabs = inBar('tabList').top >= bar.top - 1 && inBar('tabList').bottom <= bar.bottom + 1;
          const translate = inBar('btnTranslateAll');
          const inTool = translate.top >= tool.top - 1 && translate.bottom <= tool.bottom + 1;
          const active = document.querySelector('.doc-tab.active');
          const cs = getComputedStyle(active);
          return {
            tabsInBar: tabs, viewInTool: view, translateInTool: inTool,
            activeRadius: cs.borderRadius, activeBottomLeft: cs.borderBottomLeftRadius, activeBottom: cs.borderBottomWidth,
            tabBg: cs.backgroundImage, toolBg: getComputedStyle(document.getElementById('readerToolbar')).backgroundImage,
            tabAttach: cs.backgroundAttachment, toolAttach: getComputedStyle(document.getElementById('readerToolbar')).backgroundAttachment,
            barH: Math.round(bar.height), toolH: Math.round(tool.height),
            toolbarVisible: tool.height > 0,
          };
        }""")
        check("全局栏高度约 50px", 46 <= topbar_h <= 54 and layout["barH"] == round(topbar_h), f"{topbar_h:.0f}px")
        check("阅读栏高度约 46px", 40 <= toolbar_h <= 50, f"{toolbar_h:.0f}px")
        check("标签留在上层", layout["tabsInBar"])
        check("视图控件在下层", layout["viewInTool"])
        check("翻译按钮在下层", layout["translateInTool"])
        check("两层顶栏合计不超过 100px", topbar_h + toolbar_h <= 100, f"{topbar_h + toolbar_h:.0f}px")
        check("选中标签圆角只在顶部",
              layout["activeRadius"].startswith("10px") and layout["activeBottomLeft"] == "0px",
              f"{layout['activeRadius']} / {layout['activeBottomLeft']}")
        check("选中标签没有下边框", layout["activeBottom"] in ("0px", "0"), layout["activeBottom"])
        check("选中标签与阅读栏同底色",
              layout["tabBg"] == layout["toolBg"] and "gradient" in layout["tabBg"],
              f"{layout['tabBg'][:40]} vs {layout['toolBg'][:40]}")
        check("两层用的是同一张固定背景",
              layout["tabAttach"] == layout["toolAttach"] == "fixed",
              f"{layout['tabAttach']} / {layout['toolAttach']}")
        check("译文/缩放控件可见", await page.locator("#paneSwitch").is_visible()
              and await page.locator("#zoomCtl").is_visible())
        check("翻译按钮可见", await page.locator("#btnTranslateAll").is_visible())
        check("搜索/全屏按钮可见", await page.locator("#btnSearch").is_visible()
              and await page.locator("#btnFocus").is_visible())

        # 第二层「⋯」只放论文相关项
        await page.locator("#btnDocMore").click()
        await page.wait_for_timeout(150)
        check("论文菜单可打开", await page.locator("#docMenu").is_visible())
        for item in ["#fontPlus", "#btnRetranslate", "#btnExport"]:
            check(f"论文菜单包含 {item}", await page.locator(item).is_visible())
        check("论文菜单不含全局项", not await page.locator("#btnAbout").is_visible())
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        check("Esc 关闭论文菜单", not await page.locator("#docMenu").is_visible())
        check("原版视图隐藏栏目头", not await page.locator("#colhead").is_visible())

        # 同时打开多篇：每个文档一个标签
        await page.locator("#brandHome").click()
        await page.wait_for_timeout(250)
        check("品牌键回到首页标签", await page.locator("#homeView").is_visible())
        items = page.locator("#recentList .recent-item")
        await (items.nth(1) if await items.count() > 1 else items.first).click()
        await page.wait_for_selector(".page-pair", timeout=40000)
        check("空白首页标签承载第二篇", await page.locator(".doc-tab").count() == 2)

        await page.locator("#brandHome").click()
        await page.wait_for_timeout(250)
        check("没有空白标签时回首页会新建", await page.locator(".doc-tab").count() == 3)
        await page.locator("#recentList .recent-item").first.click()
        await page.wait_for_selector(".page-pair", timeout=40000)
        check("第三篇进入新标签", await page.locator(".doc-tab").count() == 3)

        await page.locator(".doc-tab").nth(0).click()
        await page.wait_for_selector(".page-pair", timeout=20000)
        titles = [t.strip() for t in await page.locator(".doc-tab-title").all_inner_texts()]
        check("切换标签后仍能渲染文档", await page.locator(".page-pair").count() >= 2)
        check("文档标签都显示文档名而不是首页",
              len(titles) == 3 and all(t and t != "首页" for t in titles),
              " / ".join(title[:18] for title in titles))

        # 全屏阅读
        await page.locator("#btnFocus").click()
        await page.wait_for_timeout(350)
        check("进入全屏阅读", await page.evaluate("() => document.body.classList.contains('focus-mode')"))
        check("全屏隐藏两层顶栏", not await page.locator("#topbar").is_visible()
              and not await page.locator("#readerToolbar").is_visible())
        check("全屏显示浮动工具条", await page.locator("#focusBar").is_visible())
        side_box = await page.locator("#sidePanel").bounding_box()
        check("目录默认移出屏幕", bool(side_box) and side_box["x"] + side_box["width"] <= 1, str(side_box))

        # 全屏里 Ctrl+F 仍然可用
        await page.keyboard.press("Control+f")
        await page.wait_for_timeout(200)
        check("全屏内 Ctrl+F 打开查找", await page.locator("#searchBar").is_visible())
        search_box = await page.locator("#searchBar").bounding_box()
        check("全屏查找框位于顶部", bool(search_box) and search_box["y"] < 90)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        check("Esc 先关闭查找而不是退出全屏",
              not await page.locator("#searchBar").is_visible()
              and await page.evaluate("() => document.body.classList.contains('focus-mode')"))

        # 左侧唤出目录
        await page.locator("#btnFocusToc").click()
        await page.wait_for_timeout(350)
        check("全屏可唤出目录", await page.evaluate("() => document.body.classList.contains('toc-open')"))
        box = await page.locator("#sidePanel").bounding_box()
        check("目录悬浮在左侧", bool(box) and box["x"] < 40, str(box))
        await page.locator("#sidePanel #toc .toc-item").first.click()
        await page.wait_for_timeout(500)
        check("选完章节自动收起目录", not await page.evaluate("() => document.body.classList.contains('toc-open')"))
        await page.locator("#btnFocusToc").click()
        await page.wait_for_timeout(350)
        check("可再次唤出目录", await page.evaluate("() => document.body.classList.contains('toc-open')"))
        await page.locator("#btnFocusToc").click()
        await page.wait_for_timeout(350)
        check("全屏可隐藏目录", not await page.evaluate("() => document.body.classList.contains('toc-open')"))

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)
        check("Esc 退出全屏阅读", not await page.evaluate("() => document.body.classList.contains('focus-mode')"))
        if await page.evaluate("() => Boolean(document.fullscreenElement)"):
            await page.evaluate("() => document.exitFullscreen().catch(() => {})")
            await page.wait_for_timeout(200)
        check("退出后顶栏恢复", await page.locator("#topbar").is_visible()
              and await page.locator("#readerToolbar").is_visible())

        # 关闭标签页
        before = await page.locator(".doc-tab").count()
        await page.keyboard.press("Alt+w")
        await page.wait_for_timeout(300)
        check("Alt+W 关闭当前标签页", await page.locator(".doc-tab").count() == before - 1)

        for _ in range(10):
            current = page.locator(".doc-tab")
            if await current.count() == 1 and "首页" in (await current.first.inner_text()):
                break
            await current.last.locator(".doc-tab-close").click()
            await page.wait_for_timeout(250)
        check("关完所有标签后回到首页", await page.locator("#homeView").is_visible())

        # 更多菜单里可以打开设置
        await page.locator("#btnMore").click()
        await page.wait_for_timeout(150)
        await page.locator("#btnSettings").click()
        await page.wait_for_timeout(400)
        check("菜单可打开翻译设置", await page.locator("#settingsModal").is_visible())
        await page.locator('#settingsModal .dialog-head [data-close="settings"]').click()
        await page.wait_for_timeout(200)

        check("无 JS 运行时错误", not errors, "; ".join(errors[:3])[:300])
        await browser.close()

    print()
    if failures:
        print("失败项：" + "、".join(failures))
        return 1
    print("多标签页与全屏阅读检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))