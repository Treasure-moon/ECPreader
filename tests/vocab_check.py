"""生词本端到端检查：收藏接口 + 查词浮层三种状态 + 生词本抽屉。

词典查询在测试里被拦截成固定数据（不联网），生词本本身走真实后端与真实 SQLite。

    .venv\\Scripts\\python.exe tests\\vocab_check.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.mkdtemp(prefix="ecp-vocab-")
os.environ["ECP_DATA_DIR"] = TEMP_DIR
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# 版本检查会联网，测试里指向一个必定失败的地址，安静降级即可
os.environ["ECP_RELEASES_API"] = "http://127.0.0.1:9/releases/latest"

import httpx  # noqa: E402
import pymupdf  # noqa: E402
import uvicorn  # noqa: E402

from app.main import TOKEN, create_app  # noqa: E402
from app.translate import dictionary  # noqa: E402

FAILURES: list[str] = []
LOOKUP = {"word": "", "sense": "", "gloss": "", "senses": [], "sentence": ""}


async def _fake_lookup(word: str, sentence: str = "", paragraph: str = "") -> dict:
    """假词典：不联网、不烧额度，词义与义项由用例指定。"""
    return {
        "word": word,
        "phonetic": "/bæŋk/",
        "pos": "n.",
        "definitions": [{"pos": "noun", "def": "a financial institution"}],
        "word_zh": "银行，河岸",
        "context_meaning": LOOKUP["gloss"],
        "senses": list(LOOKUP["senses"]),
        "base_sense": LOOKUP["sense"],
        "sentence_en": sentence or LOOKUP["sentence"],
        "sentence_zh": "（测试译文）",
        "engine": "mock",
        "warnings": [],
    }


dictionary.lookup = _fake_lookup


def find_word(overview: dict, word: str) -> dict:
    return next(item for item in overview["words"] if item["word"] == word)


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}".strip())


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_pdf(path: Path, sentence: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 80), "Vocabulary Paper", fontsize=20, fontname="hebo")
    page.insert_text((60, 120), "1. Introduction", fontsize=13, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(60, 140, 535, 300), sentence, fontsize=11)
    doc.save(str(path))
    doc.close()


def upload(base: str, headers: dict, name: str, sentence: str) -> dict:
    path = Path(TEMP_DIR) / name
    build_pdf(path, sentence)
    with open(path, "rb") as handle:
        data = handle.read()
    return httpx.post(
        f"{base}/api/documents",
        files={"file": (path.name, data, "application/pdf")},
        headers=headers,
        timeout=90,
    ).json()["document"]


def main() -> int:
    api_port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=api_port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    base = f"http://127.0.0.1:{api_port}"
    headers = {"X-ECP-Token": TOKEN}

    for _ in range(120):
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
        print("接口层：收藏的是语义而不是单词")
        empty = httpx.post(f"{base}/api/vocab/status", headers=headers,
                           json={"word": "bank", "sense": "银行", "gloss": ""}).json()
        check("没收藏过时状态为空",
              empty["count"] == 0 and empty["collected"] is False,
              json.dumps(empty, ensure_ascii=False))

        doc_a = upload(base, headers, "a.pdf", "The bank approved the loan yesterday.")
        doc_b = upload(base, headers, "b.pdf", "We sat on the bank of the river.")

        first = httpx.post(f"{base}/api/vocab", headers=headers, json={
            "word": "bank", "display": "bank", "sense": "银行", "gloss": "银行（金融机构）",
            "pos": "n.", "phonetic": "/bæŋk/", "senses": ["银行", "河岸", "存储"],
            "doc_id": doc_a["id"], "doc_title": doc_a["title"], "page": 1, "seq": 2,
            "sentence": "The bank approved the loan yesterday.",
            "sentence_zh": "银行昨天批准了这笔贷款。",
        }).json()
        check("收藏后计数为 1", first["count"] == 1 and first["collected"] is True,
              json.dumps(first, ensure_ascii=False))
        check("记下了常见义项", first["senses"] == ["银行", "河岸", "存储"], str(first["senses"]))

        other = httpx.post(f"{base}/api/vocab", headers=headers, json={
            "word": "bank", "display": "bank", "sense": "河岸", "gloss": "河岸（河边）",
            "doc_id": doc_b["id"], "doc_title": doc_b["title"], "page": 1, "seq": 2,
            "sentence": "We sat on the bank of the river.",
        }).json()
        check("同一个词可以收藏第二个语义",
              other["count"] == 2 and other["collected"] is True,
              json.dumps(other, ensure_ascii=False))

        same = httpx.post(f"{base}/api/vocab/status", headers=headers,
                          json={"word": "bank", "sense": "银行", "gloss": "银行（金融机构）"}).json()
        check("查同一个语义时是已收藏", same["collected"] is True and same["count"] == 2)
        check("同一语义不会重复收藏", len(same["entry_ids"]) == 1, str(same["entry_ids"]))

        dup = httpx.post(f"{base}/api/vocab", headers=headers, json={
            "word": "bank", "display": "bank", "sense": "银行", "gloss": "银行（金融机构）",
            "doc_id": doc_a["id"], "doc_title": doc_a["title"], "page": 1, "seq": 2,
        }).json()
        check("同一文档同一语义幂等", dup["count"] == 2 and len(dup["entry_ids"]) == 1)

        overview = httpx.get(f"{base}/api/vocab", headers=headers).json()
        check("列表按词归组",
              overview["total_words"] == 1 and overview["total_entries"] == 2,
              f'{overview["total_words"]}/{overview["total_entries"]}')
        word = find_word(overview, "bank")
        check("词条带两个来源文章", len(word["entries"]) == 2, str(len(word["entries"])))
        check("未收藏的常见义项也列出来", "存储" in word["senses"], str(word["senses"]))

        print("按单词查看用字典序")
        for name, sense in (("zebra", "斑马"), ("apple", "苹果"), ("Batch", "批次")):
            httpx.post(f"{base}/api/vocab", headers=headers, json={
                "word": name, "display": name, "sense": sense,
                "doc_id": doc_a["id"], "doc_title": doc_a["title"], "page": 1, "seq": 2,
            })
        order = [item["display"] for item in httpx.get(f"{base}/api/vocab", headers=headers).json()["words"]]
        check("单词按字典序排列", order == sorted(order, key=str.casefold), str(order))

        print("机翻标记不会进生词本")
        dirty = httpx.post(f"{base}/api/vocab", headers=headers, json={
            "word": "concatenating", "display": "concatenating",
            "sense": '<g id="1"> 得到的。<',
            "gloss": "拼接（特征）",
            "senses": ['<g id="1"> 得到的。<', 'g><g id="2">!<', "g>", "拼接"],
            "doc_id": doc_a["id"], "doc_title": doc_a["title"], "page": 1, "seq": 2,
        }).json()
        concat = [item for item in httpx.get(f"{base}/api/vocab", headers=headers).json()["words"]
                  if item["word"] == "concatenating"][0]
        check("脏直译换成语境示意", concat["entries"][0]["sense"] == "拼接（特征）",
              concat["entries"][0]["sense"])
        check("义项里没有标签残片",
              all("<" not in item and ">" not in item for item in concat["senses"]),
              str(concat["senses"]))

        print("历史脏数据启动时会被洗掉")
        from app import db as _db  # noqa: E402
        from app import vocab as _vocab  # noqa: E402

        conn = _db._connect()
        conn.execute("UPDATE vocab_entries SET sense=? WHERE word='concatenating'",
                     ('<g id="1"> 得到的。<',))
        conn.execute("UPDATE vocab_words SET senses_json=? WHERE word='concatenating'",
                     (_db._dump(['<g id="1"> 得到的。<', 'g><g id="2">!<', 'g>']),))
        conn.commit()
        fixed = _vocab.sanitize_stored()
        after = _vocab.word_entries("concatenating")[0]
        check("洗掉数量大于 0", fixed >= 1, str(fixed))
        check("脏直译被修好", after["sense"] == "拼接（特征）", after["sense"])
        check("脏义项被剔除", _vocab.word_senses("concatenating") == [], str(_vocab.word_senses("concatenating")))
        check("重复清洗没有副作用", _vocab.sanitize_stored() == 0)

        category = httpx.post(f"{base}/api/vocab/categories", headers=headers,
                              json={"name": "方法学"}).json()
        bank_entry = [item for item in word["entries"] if item["sense"] == "银行"][0]
        httpx.post(f"{base}/api/vocab/{bank_entry['id']}/category", headers=headers,
                   json={"category_id": category["id"], "word": "bank"})
        overview = httpx.get(f"{base}/api/vocab", headers=headers).json()
        assigned = [item for item in find_word(overview, "bank")["entries"] if item["id"] == bank_entry["id"]][0]
        check("分类可以指派", assigned["category_name"] == "方法学", str(assigned["category_name"]))

        print("分类可以删除")
        spare = httpx.post(f"{base}/api/vocab/categories", headers=headers,
                           json={"name": "临时分类"}).json()
        httpx.post(f"{base}/api/vocab/{bank_entry['id']}/category", headers=headers,
                   json={"category_id": spare["id"], "word": "bank"})
        gone = httpx.delete(f"{base}/api/vocab/categories/{spare['id']}", headers=headers).json()
        check(
            "接口能删掉分类",
            gone.get("ok") is True and spare["id"] not in {c["id"] for c in gone.get("categories", [])},
            json.dumps(gone, ensure_ascii=False),
        )
        left = [item for item in httpx.get(f"{base}/api/vocab/categories", headers=headers).json()["categories"]
                if item["id"] == spare["id"]]
        check("分类真的没了", not left)
        re_overview = httpx.get(f"{base}/api/vocab", headers=headers).json()
        moved = [item for item in find_word(re_overview, "bank")["entries"] if item["id"] == bank_entry["id"]][0]
        check("分类里的收藏回到未分类", not moved["category_id"], str(moved["category_id"]))
        httpx.post(f"{base}/api/vocab/{bank_entry['id']}/category", headers=headers,
                   json={"category_id": category["id"], "word": "bank"})

        csv = httpx.get(f"{base}/api/vocab/export?fmt=csv", headers=headers)
        body = csv.content.decode("utf-8-sig")
        check("CSV 带 BOM 便于 Excel 打开", csv.content.startswith(b"\xef\xbb\xbf"))
        check("CSV 里有语义与来源", "河岸" in body and doc_b["title"] in body, body[:80])
        md = httpx.get(f"{base}/api/vocab/export?fmt=md", headers=headers)
        check("Markdown 导出带词条标题", "## bank" in md.text, md.text[:60])

        removed = httpx.post(f"{base}/api/vocab/uncollect", headers=headers,
                             json={"word": "bank", "sense": "银行", "gloss": "银行（金融机构）"}).json()
        check("取消收藏只删这个语义",
              removed["removed"] == 1 and removed["count"] == 1,
              json.dumps(removed, ensure_ascii=False))

        print("只给直译、只给语境示意都能对上")
        httpx.post(f"{base}/api/vocab", headers=headers, json={
            "word": "loan", "display": "loan", "sense": "贷款", "gloss": "贷款（借款）",
            "doc_id": doc_a["id"], "doc_title": doc_a["title"], "page": 1, "seq": 3,
        })
        only_sense = httpx.post(f"{base}/api/vocab/status", headers=headers,
                                json={"word": "loan", "sense": "贷款", "gloss": ""}).json()
        check("只给直译也能认出已收藏", only_sense["collected"] is True,
              json.dumps(only_sense, ensure_ascii=False))
        only_gloss = httpx.post(f"{base}/api/vocab/status", headers=headers,
                                json={"word": "loan", "sense": "", "gloss": "贷款（借款）"}).json()
        check("只给语境示意也能认出已收藏", only_gloss["collected"] is True)
        off = httpx.post(f"{base}/api/vocab/uncollect", headers=headers,
                         json={"word": "loan", "sense": "贷款", "gloss": ""}).json()
        check("只给直译也能取消收藏", off["removed"] == 1, json.dumps(off, ensure_ascii=False))

        print("文档删除后的收藏")
        gone_doc = upload(base, headers, "gone.pdf", "The bank of the river is steep.")
        httpx.post(f"{base}/api/vocab", headers=headers, json={
            "word": "river", "display": "river", "sense": "河流", "gloss": "河（水域）",
            "senses": ["河流", "河"], "doc_id": gone_doc["id"], "doc_title": gone_doc["title"],
            "page": 1, "seq": 1, "sentence": "The bank of the river is steep.",
        })
        httpx.delete(f"{base}/api/documents/{gone_doc['id']}", headers=headers)
        overview = httpx.get(f"{base}/api/vocab", headers=headers).json()
        river = [item for item in overview["words"] if item["word"] == "river"][0]
        check("文章删除后标记为丢失", river["entries"][0]["alive"] is False)

        print("浏览器层：查词浮层的三种状态")
        import asyncio

        asyncio.run(browser_checks(base, headers, doc_b))
    finally:
        server.should_exit = True

    if FAILURES:
        print(f"\n生词本检查失败 {len(FAILURES)} 项")
        return 1
    print("生词本检查全部通过")
    return 0


async def browser_checks(base: str, headers: dict, doc_b: dict) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="msedge", headless=True)
        context = await browser.new_context()
        await context.add_init_script("localStorage.setItem('ecp.viewMode', 'flow');")
        page = await context.new_page()

        await page.goto(f"{base}/", wait_until="networkidle")
        await page.wait_for_selector("#recentList .recent-item", timeout=20000)
        cards = await page.eval_on_selector_all(".feature h3", "nodes => nodes.map(n => n.textContent.trim())")
        check("首页卖点卡补全到 8 个", len(cards) == 8, str(cards))
        check("首页有内置 AI 助手", "内置 AI 助手" in cards, str(cards))
        check("首页有生词本", "生词本" in cards, str(cards))
        await page.click(f"#recentList .recent-item[data-doc-id='{doc_b['id']}']")
        await page.wait_for_selector("#readerView:not([hidden])", timeout=25000)
        await page.wait_for_selector(".pane.src .seg", timeout=20000)

        async def select_word(word: str) -> bool:
            return bool(
                await page.evaluate(
                    """(word) => {
                        const segs = [...document.querySelectorAll('.pane.src .seg')];
                        const seg = segs.find((node) => node.textContent.toLowerCase().includes(word.toLowerCase()));
                        if (!seg) return false;
                        const walker = document.createTreeWalker(seg, NodeFilter.SHOW_TEXT);
                        let node = walker.nextNode();
                        while (node) {
                            const index = node.textContent.toLowerCase().indexOf(word.toLowerCase());
                            if (index >= 0) {
                                const range = document.createRange();
                                range.setStart(node, index);
                                range.setEnd(node, index + word.length);
                                const selection = window.getSelection();
                                selection.removeAllRanges();
                                selection.addRange(range);
                                document.dispatchEvent(new Event('selectionchange'));
                                return true;
                            }
                            node = walker.nextNode();
                        }
                        return false;
                    }""",
                    word,
                )
            )

        LOOKUP.update(word="bank", sense="银行", gloss="银行（金融机构）",
                      senses=["银行", "河岸", "存储"],
                      sentence="The bank approved the loan yesterday.")
        check("能选中单词并弹出浮层", await select_word("bank"))
        await page.wait_for_selector("#wordPopover .wp-book", timeout=15000)
        await page.wait_for_function(
            "() => document.querySelector('#wordPopover .wp-book')?.dataset.state !== 'empty'",
            timeout=8000,
        )
        state = await page.get_attribute("#wordPopover .wp-book", "data-state")
        check("这个词已收藏过别的语义时显示数字圈", state == "more", str(state))
        number = (await page.inner_text("#wordPopover .wp-book .bk-num")).strip()
        check("数字圈显示已收藏的语义数", number == "1", number)

        base_display = await page.evaluate(
            "() => getComputedStyle(document.querySelector('#wordPopover .wp-book .bk-plus')).display"
        )
        check("默认显示数字圈（+1 圈收起）", base_display == "none", str(base_display))
        await page.hover("#wordPopover .wp-book")
        await page.wait_for_timeout(220)
        hover = await page.evaluate(
            """() => {
                const btn = document.querySelector('#wordPopover .wp-book');
                const plus = getComputedStyle(btn.querySelector('.bk-plus')).display;
                const num = getComputedStyle(btn.querySelector('.bk-num')).display;
                return {plus, num, hovered: btn.matches(':hover')};
            }"""
        )
        check("悬停后变成圆圈 +1",
              hover["hovered"] and hover["plus"] in {"flex", "inline-flex"} and hover["num"] == "none",
              str(hover))
        await page.mouse.move(0, 0)
        await page.wait_for_timeout(150)

        await page.click("#wordPopover .wp-book")
        await page.wait_for_function(
            "() => document.querySelector('#wordPopover .wp-book')?.dataset.state === 'done'",
            timeout=10000,
        )
        check("收藏后变成圆圈勾", True)
        collected = httpx.post(f"{base}/api/vocab/status", headers=headers,
                               json={"word": "bank", "sense": "银行",
                                     "gloss": "银行（金融机构）"}).json()
        check("收藏写进了后端", collected["collected"] is True and collected["count"] == 2,
              json.dumps(collected, ensure_ascii=False))
        entry_doc = collected["matches"][0] if collected.get("matches") else {}
        check("收藏记住了来源文章", entry_doc.get("doc_title") == doc_b["title"],
              json.dumps(entry_doc.get("doc_title"), ensure_ascii=False))

        await page.click("#wordPopover .wp-book")
        await page.wait_for_function(
            "() => document.querySelector('#wordPopover .wp-book')?.dataset.state === 'more'",
            timeout=10000,
        )
        after = httpx.post(f"{base}/api/vocab/status", headers=headers,
                           json={"word": "bank", "sense": "银行",
                                 "gloss": "银行（金融机构）"}).json()
        check("再点一次取消收藏", after["collected"] is False and after["count"] == 1,
              json.dumps(after, ensure_ascii=False))
        await page.keyboard.press("Escape")

        print("浏览器层：生词本抽屉")
        order = await page.evaluate("""() => [...document.querySelectorAll('.top-actions > *')]
            .map((node) => node.id)""")
        check(
            "生词本图标在翻译设置和关于之间",
            order.index("btnVocab") == order.index("engineBadge") + 1
            and order.index("btnAboutTop") == order.index("btnVocab") + 1,
            str(order),
        )
        check("顶栏只放一个小图标", await page.inner_text("#engineBadge") in ("AI", "机翻", "未配置"),
              await page.inner_text("#engineBadge"))
        await page.click("#btnVocab")
        await page.wait_for_selector("#vocabDrawer:not([hidden]) .vocab-word", timeout=15000)
        text = await page.inner_text("#vocabList")
        check("抽屉里列出收藏的单词", "bank" in text.lower(), text[:60])
        check("收藏的语义加粗显示", await page.locator(".vsense-kept .vsense-term").count() >= 1)
        check("没收藏的常见义项也在", "银行" in text or "存储" in text, text[:120])

        term = page.locator(".vocab-word[data-word='bank'] .vsense-kept .vsense-term").first
        await term.hover()
        await page.wait_for_selector("#vocabTip:not([hidden])", timeout=5000)
        tip = await page.inner_text("#vocabTip")
        check("悬停显示来源文章与时间", "收藏于" in tip and doc_b["title"] in tip, tip[:90])

        print("点单词进单词语义详情")
        term = page.locator(".vocab-word[data-word='bank'] .vsense-kept .vsense-term").first
        await term.click()
        await page.wait_for_selector(".vocab-detail .vocab-word[data-word='bank']", timeout=8000)
        check("详情页只显示这一个单词", await page.locator("#vocabList .vocab-word").count() == 1)
        check("详情页保留来源文章标题", doc_b["title"] in await page.inner_text("#vocabList"))
        check("详情页有返回按钮", await page.locator(".vocab-back").count() == 1)
        await page.click(".vocab-back")
        await page.wait_for_timeout(400)
        check(
            "返回回到单词列表",
            await page.locator(".vocab-detail").count() == 0
            and await page.locator("#vocabList .vocab-word").count() >= 1,
        )

        labels = await page.inner_text("#vocabGroup")
        check(
            "标签只剩单词/分类/时间/文章",
            all(key in labels for key in ("单词", "分类", "时间", "文章")) and "按" not in labels,
            labels,
        )

        modes = (("category", "按分类", "未分类"), ("doc", "按文章", doc_b["title"]), ("time", "按时间", ""))
        for mode, label, expect in modes:
            await page.click(f"#vocabGroup [data-group='{mode}']")
            await page.wait_for_timeout(350)
            listing = await page.inner_text("#vocabList")
            if expect:
                check(f"可以{label}查看", expect in listing, listing[:80])
            else:
                check("可以按时间查看", any(label in listing for label in ("今天", "昨天", "20")), listing[:80])

        print("按文章：拖到分类可以整篇改分类")
        await page.click("#vocabGroup [data-group='doc']")
        await page.wait_for_timeout(350)
        doc_head = page.locator(f".vg-doc[data-doc='{doc_b['id']}']")
        await doc_head.wait_for(timeout=8000)
        check("文章分组有打开按钮", await page.locator(".vg-doc .vg-open").count() >= 1)
        check("拖到分类的投放条出现", await page.locator("#vocabDrops .vd-chip").count() >= 1)
        await page.evaluate(
            """(docId) => {
                const head = document.querySelector(`.vg-doc[data-doc='${docId}']`);
                const chip = [...document.querySelectorAll('.vd-chip')]
                    .find((node) => node.textContent.trim() === '方法学');
                const dt = new DataTransfer();
                head.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
                chip.dispatchEvent(new DragEvent('dragover', {bubbles: true, dataTransfer: dt}));
                chip.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: dt}));
            }""",
            doc_b["id"],
        )
        await page.wait_for_timeout(1200)
        overview = httpx.get(f"{base}/api/vocab", headers=headers).json()
        moved = [item for word in overview["words"] for item in word["entries"]
                 if item["doc_id"] == doc_b["id"]]
        check(
            "整篇收藏一次改成新分类",
            bool(moved) and all(item["category_name"] == "方法学" for item in moved),
            json.dumps([item["category_name"] for item in moved], ensure_ascii=False),
        )
        bad = httpx.post(f"{base}/api/vocab/categories/assign", headers=headers,
                         json={"entry_ids": [moved[0]["id"]], "category_id": "nope"})
        check("改到不存在的分类会被拒", bad.status_code == 400, str(bad.status_code))

        print("按文章：按钮只打开这篇文章")
        await page.locator(f".vg-doc[data-doc='{doc_b['id']}'] .vg-open").click()
        await page.wait_for_selector("#readerView:not([hidden])", timeout=20000)
        await page.wait_for_timeout(900)
        active = await page.inner_text(".doc-tab.active")
        check("打开了这篇文章", doc_b["title"][:8] in active, active[:60])
        check(
            "打开文章后没有跳到某一段",
            await page.evaluate("() => document.querySelectorAll('.brow.flash, .ov-block.flash').length === 0"),
        )

        print("分类视图：把单词拖到另一个分类下")
        # 先建一个空分类当投放目标，再打开抽屉（打开时才会重新取数据）
        httpx.post(f"{base}/api/vocab/categories", headers=headers, json={"name": "拖拽目标"})
        cats = {item["name"]: item["id"] for item in
                httpx.get(f"{base}/api/vocab/categories", headers=headers).json()["categories"]}
        await page.click("#btnVocab")
        await page.wait_for_selector("#vocabDrawer:not([hidden])", timeout=8000)
        await page.click("#vocabGroup [data-group='category']")
        await page.wait_for_timeout(600)
        empty_group = page.locator(".vocab-group", has=page.locator(".vocab-group-head", has_text="拖拽目标"))
        await empty_group.wait_for(timeout=8000)
        check("空分类也显示出来当投放目标", await empty_group.locator(".vocab-group-empty").count() == 1)
        await page.evaluate(
            """([srcId, dstName]) => {
                const row = document.querySelector(`.vocab-group[data-key="${srcId}"] .vocab-row`);
                const section = [...document.querySelectorAll('.vocab-group')]
                    .find((node) => node.querySelector('.vocab-group-head')?.textContent.includes(dstName));
                const dt = new DataTransfer();
                row.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
                section.dispatchEvent(new DragEvent('dragover', {bubbles: true, dataTransfer: dt}));
                section.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: dt}));
            }""",
            [cats["方法学"], "拖拽目标"],
        )
        await page.wait_for_timeout(1200)
        moved_in = [item for word in httpx.get(f"{base}/api/vocab", headers=headers).json()["words"]
                    for item in word["entries"] if item["category_name"] == "拖拽目标"]
        check("单词被拖进了目标分类", bool(moved_in),
              json.dumps([item["word"] for item in moved_in], ensure_ascii=False))

        print("分类可以改名")
        await page.click("#btnVocabCats")
        cat_row = page.locator("#vocabCatList .vocab-cat",
                               has=page.locator('input[value="拖拽目标"]'))
        await cat_row.wait_for(timeout=8000)
        await cat_row.locator("input").fill("改名后的分类")
        await page.locator("#vocabCatInput").click()
        await page.wait_for_timeout(1200)
        renamed = httpx.get(f"{base}/api/vocab/categories", headers=headers).json()["categories"]
        check("改名写进了后端", any(item["name"] == "改名后的分类" for item in renamed),
              json.dumps([item["name"] for item in renamed], ensure_ascii=False))
        check("改名后界面跟着变", await page.locator('#vocabCatList input[value="改名后的分类"]').count() == 1)
        await page.click("#btnVocabCats")

        print("分类编辑器里能删除分类")
        if await page.locator("#vocabDrawer").is_hidden():
            await page.click("#btnVocab")
        await page.wait_for_selector("#vocabDrawer:not([hidden])", timeout=8000)
        # 删除前会弹确认框，测试里直接放过（确认文案不属于这里要验的东西）
        await page.evaluate("window.confirm = () => true")
        await page.click("#btnVocabCats")
        await page.wait_for_selector("#vocabCatList .vocab-cat")
        await page.fill("#vocabCatInput", "待删分类")
        await page.click("#btnVocabCatAdd")
        target = page.locator(
            "#vocabCatList .vocab-cat", has=page.locator('input[value="待删分类"]')
        )
        await target.wait_for(timeout=8000)
        await target.locator('[data-act="del-cat"]').click()
        await page.wait_for_timeout(800)
        check("界面能删掉分类", await target.count() == 0)
        cats_now = httpx.get(f"{base}/api/vocab/categories", headers=headers).json()["categories"]
        check("删除同步到后端", not [c for c in cats_now if c["name"] == "待删分类"])

        check("导出入口存在", await page.locator("#btnVocabExportCsv").count() == 1)
        await page.keyboard.press("Escape")

        print("浏览器层：原版视图双击也能收藏")
        layout_ctx = await browser.new_context(viewport={"width": 1500, "height": 950})
        layout = await layout_ctx.new_page()
        await layout.goto(f"{base}/", wait_until="networkidle")
        await layout.wait_for_selector("#recentList .recent-item", timeout=20000)
        await layout.click(f"#recentList .recent-item[data-doc-id='{doc_b['id']}']")
        await layout.wait_for_selector(".textlayer .w[data-sid]", timeout=25000)
        word_el = layout.locator(".textlayer .w[data-sid]").nth(3)
        label = (await word_el.inner_text()).strip()
        await word_el.dblclick()
        await layout.wait_for_selector("#wordPopover .wp-book", timeout=15000)
        await layout.wait_for_function(
            "() => document.querySelector('#wordPopover .wp-book')?.dataset.state === 'empty'",
            timeout=8000,
        )
        check("原版视图双击后浮层有收藏按钮", True, label)
        await layout.click("#wordPopover .wp-book")
        await layout.wait_for_function(
            "() => document.querySelector('#wordPopover .wp-book')?.dataset.state === 'done'",
            timeout=10000,
        )
        saved = httpx.post(f"{base}/api/vocab/status", headers=headers,
                           json={"word": label.lower(), "sense": LOOKUP["sense"],
                                 "gloss": LOOKUP["gloss"]}).json()
        saved_meta = (saved.get("matches") or [{}])[0]
        check("原版视图收藏记住页码与段落",
              saved.get("collected") is True and saved_meta.get("doc_id") == doc_b["id"]
              and saved_meta.get("page") == 1 and bool(saved_meta.get("seq")),
              json.dumps({k: saved_meta.get(k) for k in ("doc_id", "page", "seq")}, ensure_ascii=False))
        await layout_ctx.close()

        await browser.close()


if __name__ == "__main__":
    sys.exit(main())