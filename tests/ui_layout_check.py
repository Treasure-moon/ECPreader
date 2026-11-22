"""原版布局视图的端到端检查（Edge headless）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8791/"
SHOTS = Path(__file__).parent / "shots"
SHOTS.mkdir(exist_ok=True)

SELECT_WORDS = """() => {
  const words = [...document.querySelectorAll('.textlayer .w[data-sid]')];
  const bySeq = new Map();
  for (const word of words) {
    const seq = word.dataset.seq || '';
    if (!bySeq.has(seq)) bySeq.set(seq, []);
    bySeq.get(seq).push(word);
  }
  const overlaySeqs = new Set([...document.querySelectorAll('.ov-block[data-seq]')].map((node) => node.dataset.seq));
  const list = [...bySeq.entries()]
    .filter(([seq, items]) => items.length > 6 && seq && overlaySeqs.has(seq))
    .map(([, items]) => items)[0];
  if (!list) return null;
  const start = list[1];
  const end = list[4];
  const range = document.createRange();
  range.setStart(start.firstChild, 0);
  range.setEnd(end.firstChild, Math.min(4, end.firstChild.length));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return {
    seq: start.dataset.seq,
    sid: start.dataset.sid,
    text: selection.toString().slice(0, 60),
  };
}"""

SELECT_CHINESE = """() => {
  const spans = [...document.querySelectorAll('.ov-block [data-sid]')].filter((s) => s.textContent.trim().length > 3);
  if (!spans.length) return null;
  const span = spans[0];
  const range = document.createRange();
  range.setStart(span.firstChild, 0);
  range.setEnd(span.firstChild, Math.min(4, span.firstChild.length));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return { seq: span.closest('.ov-block').dataset.seq, text: selection.toString() };
}"""

SELECT_SENTENCE = """() => {
  const segs = [...document.querySelectorAll('.brow .pane.src .seg')].filter((s) => s.textContent.trim().length > 20);
  if (!segs.length) return null;
  const seg = segs[0];
  const range = document.createRange();
  range.setStart(seg.firstChild, 0);
  range.setEnd(seg.firstChild, Math.min(20, seg.firstChild.length));
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  return { sid: seg.dataset.sid };
}"""


async def main() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(("PASS " if condition else "FAIL ") + name + (f" · {detail}" if detail else ""))
        if not condition:
            failures.append(name)

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context(viewport={"width": 1500, "height": 1000})
        await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE.rstrip("/"))
        page = await context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        page.on("response", lambda r: errors.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)

        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_selector("#recentList .recent-item", timeout=20000)
        items = page.locator("#recentList .recent-item")
        count = await items.count()
        check("最近文档列表有内容", count >= 1, f"{count} 条")
        check(
            "首页不再散落反馈邮箱",
            await page.locator(".feedback-line").count() == 0,
        )
        await page.locator("#btnAbout").click()
        await page.wait_for_timeout(200)
        about_text = await page.locator("#aboutModal").inner_text()
        check("关于界面可打开", await page.locator("#aboutModal").is_visible())
        check("关于界面显示版本", "1.4.1" in about_text, about_text[:40].replace("\n", " / "))
        check("关于界面显示作者与邮箱", "Treasure_moon" in about_text and "treasure_moon@163.com" in about_text)
        check("关于界面显示 GitHub 与 Star 引导", "github.com/Treasure-moon/ECPreader" in about_text and "Star" in about_text)
        await page.locator('#aboutModal [data-close="about"]').last.click()
        await page.wait_for_timeout(150)
        check("关于界面可关闭", not await page.locator("#aboutModal").is_visible())

        target = None
        for index in range(count):
            text = await items.nth(index).inner_text()
            if "Segment" in text:
                target = items.nth(index)
                break
        target = target or items.nth(0)
        await target.click()

        await page.wait_for_selector(".page-pair", timeout=40000)
        await page.wait_for_function(
            "() => { const img = document.querySelector('.page-img'); return img && img.complete && img.naturalWidth > 100; }",
            timeout=40000,
        )
        pairs = await page.locator(".page-pair").count()
        check("每页一组双栏页面", pairs >= 2, f"{pairs} 组")
        check("视图栏可见", await page.locator("#viewBar").is_visible())
        check("双页按钮高亮", "active" in (await page.locator('#paneSwitch [data-pane="dual"]').get_attribute("class") or ""))

        # 侧栏收起后必须能重新打开
        await page.locator("#btnToggleSide").click()
        await page.wait_for_timeout(250)
        collapsed = await page.evaluate("() => document.getElementById('readerView').classList.contains('side-collapsed')")
        check("侧栏可收起", collapsed)
        check("收起后顶部目录按钮可见", await page.locator("#btnToggleSideTop").is_visible())
        await page.locator("#btnToggleSideTop").click()
        await page.wait_for_timeout(250)
        expanded = not await page.evaluate("() => document.getElementById('readerView').classList.contains('side-collapsed')")
        check("侧栏可重新展开", expanded)
        check("展开后目录侧栏可见", await page.locator("#sidePanel").is_visible())

        await page.wait_for_function(
            "() => document.querySelectorAll('.textlayer .w').length > 50",
            timeout=30000,
        )
        words = await page.locator(".textlayer .w").count()
        overlays = await page.locator(".ov-block").count()
        check("原文页有可选中文字层", words > 50, f"{words} 个单词")
        check("译文页有覆盖译文", overlays > 0, f"{overlays} 段")
        check("原文不再有点击热区遮挡", await page.locator(".hit").count() == 0)

        font_size = await page.locator(".ov-block").first.evaluate(
            "el => parseFloat(getComputedStyle(el).fontSize)"
        )
        check("译文按页面比例排版", 6 < font_size < 30, f"{font_size:.1f}px")

        disabled = await page.locator("#btnBlockCopy").is_disabled()
        check("未选中时复制按钮禁用", disabled)

        # 点击单词 → 段落联动 + 工具栏按钮可用（选一个确实有译文的段落）
        await page.evaluate("""() => {
          const word = [...document.querySelectorAll('.textlayer .w[data-seq]')]
            .find((node) => document.querySelector(`.ov-block[data-seq="${node.dataset.seq}"]`));
          word?.click();
        }""")
        await page.wait_for_timeout(250)
        active_ov = await page.locator(".ov-block.active").count()
        check("点击段落后译文高亮", active_ov >= 1, f"{active_ov}")
        check("选中段落后按钮可用", not await page.locator("#btnBlockCopy").is_disabled())

        # 没有选区时复制整段（中英对照）
        await page.evaluate("() => window.getSelection().removeAllRanges()")
        await page.wait_for_timeout(150)
        await page.locator("#btnBlockCopy").click()
        await page.wait_for_timeout(350)
        try:
            block_copied = await page.evaluate("navigator.clipboard.readText()")
        except Exception:  # noqa: BLE001
            block_copied = ""
        has_chinese = any(0x4E00 <= ord(ch) <= 0x9FFF for ch in block_copied)
        check("复制整段中英对照", block_copied.count(chr(10)) >= 2 and has_chinese,
              block_copied[:40].replace(chr(10), " / "))

        # 划选英文 → 右侧对应句子高亮
        await page.evaluate(SELECT_WORDS)
        await page.wait_for_timeout(500)
        synced_zh = await page.locator(".ov-block [data-sid].sync").count()
        active_en = await page.locator(".textlayer .w.active").count()
        check("选中英文后译文同步高亮", synced_zh >= 1, f"{synced_zh} 处")
        check("选中英文后选中句高亮", active_en >= 1, f"{active_en} 词")
        active_bg = await page.locator(".textlayer .w.active").first.evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        check("选中句使用灰色底", active_bg.startswith("rgba(96, 104, 120"), active_bg)

        # 工具栏复制选中内容
        await page.locator("#btnBlockCopy").click()
        await page.wait_for_timeout(400)
        try:
            copied = await page.evaluate("navigator.clipboard.readText()")
        except Exception:  # noqa: BLE001
            copied = ""
        check("复制选中文字", bool(copied.strip()), copied.strip()[:40])

        # 选中中文 → 左侧英文单词高亮
        await page.evaluate(SELECT_CHINESE)
        await page.wait_for_timeout(500)
        synced_en = await page.locator(".textlayer .w.sync").count()
        active_zh = await page.locator(".ov-block [data-sid].active").count()
        check("选中中文后英文同步高亮", synced_en >= 1, f"{synced_en} 词")
        check("选中中文后选中句高亮", active_zh >= 1, f"{active_zh} 句")
        active_zh_bg = await page.locator(".ov-block [data-sid].active").first.evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        check("中文选中句使用灰色底", active_zh_bg.startswith("rgba(96, 104, 120"), active_zh_bg)
        check(
            "选中中文时英文不再整段灰底",
            await page.locator(".textlayer .w.active").count() == 0,
        )
        whole_sentence = await page.evaluate("""() => {
          const active = document.querySelector('.ov-block [data-sid].active');
          if (!active) return null;
          const sid = active.dataset.sid;
          const all = [...document.querySelectorAll(`.textlayer .w[data-sid="${sid}"]`)];
          return { total: all.length, synced: all.filter((node) => node.classList.contains('sync')).length };
        }""")
        check(
            "中文选词后对应整句英文联动",
            bool(whole_sentence and whole_sentence["total"] > 0 and whole_sentence["total"] == whole_sentence["synced"]),
            str(whole_sentence),
        )

        # 跨句选择：每一句单独高亮，不连成一条
        cross = await page.evaluate("""() => {
          const spans = [...document.querySelectorAll('.ov-block [data-sid]')]
            .filter((span) => span.textContent.trim().length > 5);
          const first = spans[0];
          const second = spans.find((span) => span.dataset.sid !== first?.dataset.sid
            && span.closest('.page-pair') === first?.closest('.page-pair'));
          if (!first || !second) return null;
          const range = document.createRange();
          range.setStart(first.firstChild, 0);
          range.setEnd(second.firstChild, Math.min(5, second.firstChild.length));
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          return { sids: [first.dataset.sid, second.dataset.sid] };
        }""")
        await page.wait_for_timeout(500)
        if cross:
            cross_active = await page.locator(".ov-block [data-sid].active").count()
            separated = await page.evaluate("""() => {
              const nodes = [...document.querySelectorAll('.ov-block [data-sid].active')];
              return nodes.length >= 2 && nodes.every((node) => getComputedStyle(node).boxShadow !== 'none');
            }""")
            check("跨句选择按句分别高亮", cross_active >= 2, f"{cross_active} 句")
            check("跨句高亮之间有分隔", separated)
        else:
            check("跨句选择按句分别高亮", False, "没有可测试的两句")
            check("跨句高亮之间有分隔", False, "没有可测试的两句")
        await page.evaluate("() => window.getSelection().removeAllRanges()")
        await page.wait_for_timeout(200)

        # 双击查词（选一个纯英文单词，避免页码/编号）
        point = await page.evaluate("""() => {
          const el = [...document.querySelectorAll('.textlayer .w[data-sid]')]
            .find((node) => /^[A-Za-z]{3,}$/.test(node.textContent.trim()));
          if (!el) return null;
          const rect = el.getBoundingClientRect();
          return { x: rect.x + rect.width / 2, y: rect.y + Math.min(4, rect.height / 2), word: el.textContent.trim() };
        }""")
        if point:
            try:
                await page.mouse.dblclick(point["x"], point["y"])
                await page.wait_for_function(
                    "() => { const n = document.querySelector('#wordPopover:not([hidden]) .wp-word');"
                    " return n && n.textContent.trim() && !/识别中|查询失败/.test(n.textContent); }",
                    timeout=25000,
                )
                word = await page.locator("#wordPopover .wp-word").inner_text()
                check("双击单词弹出释义", bool(word.strip()), word.strip()[:24])
            except Exception as exc:  # noqa: BLE001
                check("双击单词弹出释义", False, str(exc)[:80])
            await page.keyboard.press("Escape")
        else:
            check("双击单词弹出释义", False, "没有找到纯英文单词")

        # 缩放
        width_before = await page.locator(".page-pane.src").first.evaluate("el => el.getBoundingClientRect().width")
        await page.locator("#zoomIn").click()
        await page.wait_for_timeout(250)
        width_after = await page.locator(".page-pane.src").first.evaluate("el => el.getBoundingClientRect().width")
        check("放大后页面变宽", width_after > width_before + 8, f"{width_before:.0f} → {width_after:.0f}")
        check("缩放百分比更新", (await page.locator("#zoomLabel").inner_text()).strip() == "110%")
        await page.locator("#zoomFit").click()

        # 只看译文
        await page.locator('#paneSwitch [data-pane="tgt"]').click()
        await page.wait_for_timeout(200)
        src_visible = await page.locator(".page-pane.src").first.is_visible()
        tgt_visible = await page.locator(".page-pane.tgt").first.is_visible()
        check("只看译文时隐藏原文", (not src_visible) and tgt_visible)
        await page.locator('#paneSwitch [data-pane="dual"]').click()

        # 切换逐句对照
        await page.locator('#viewSwitch [data-view="flow"]').click()
        await page.wait_for_selector(".brow", timeout=15000)
        brows = await page.locator(".brow").count()
        check("逐句对照仍有内容", brows > 10, f"{brows} 段")
        check("逐句模式显示栏目头", await page.locator("#colhead").is_visible())
        check("逐句模式隐藏缩放", not await page.locator("#zoomCtl").is_visible())
        check("逐句模式不再有悬浮按钮", await page.locator(".row-tools").count() == 0)

        await page.evaluate(SELECT_SENTENCE)
        await page.wait_for_timeout(500)
        flow_sync = await page.locator(".seg.sync").count()
        check("逐句模式划选仍联动", flow_sync >= 1, f"{flow_sync} 处")
        check("逐句模式工具栏复制可用", not await page.locator("#btnBlockCopy").is_disabled())

        # 逐句模式的部分选中：选区不落在整句上，复制内容应与选区一致
        partial = await page.evaluate("() => window.getSelection().toString()")
        await page.locator("#btnBlockCopy").click()
        await page.wait_for_timeout(350)
        try:
            partial_copied = await page.evaluate("navigator.clipboard.readText()")
        except Exception:  # noqa: BLE001
            partial_copied = ""
        check("逐句模式可复制部分文字",
              len(partial.strip()) >= 5 and partial_copied.strip()[:12] == partial.strip()[:12],
              f"{partial.strip()[:16]} → {partial_copied.strip()[:16]}")

        await page.locator('#viewSwitch [data-view="layout"]').click()
        await page.wait_for_selector(".page-pair", timeout=20000)

        # 设置页的数据目录；作者邮箱已集中到关于界面
        await page.locator("#btnSettings").click()
        await page.wait_for_timeout(400)
        await page.locator('.tab[data-tab="data"]').click()
        await page.wait_for_timeout(200)
        data_path = (await page.locator("#dataDirPath").inner_text()).strip()
        check("数据与安全显示数据目录", len(data_path) > 3 and ("\\" in data_path or "/" in data_path), data_path[:40])
        check("设置页不再散落反馈邮箱", await page.locator("#settingsModal .feedback-line").count() == 0)
        await page.locator('#settingsModal .dialog-head [data-close="settings"]').click()
        await page.wait_for_timeout(200)

        check("无 JS 运行时错误", not errors, "; ".join(errors[:3])[:200])
        await page.screenshot(path=str(SHOTS / "layout_view.png"), full_page=False)
        await browser.close()

    print()
    if failures:
        print("失败项：" + "、".join(failures))
        return 1
    print("原版布局检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
