"""目录清洗回归测试：页眉、图注、公式残片、作者行、图片刻度都不该进目录。

不依赖真实论文：现场生成一个带「页眉 + 图注 + 刻度标签 + 正常章节」的 PDF，
解析后检查结果。运行：

    .venv\\Scripts\\python.exe tests\\toc_check.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from app.parsers import common  # noqa: E402
from app.parsers.pdf_parser import parse_pdf  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILURES.append(f"{name} {detail}".strip())


# ------------------------------------------------------------------ 目录规则
def test_entry_rules() -> None:
    print("目录项规则")
    keep = [
        "I. INTRODUCTION",
        "A. Framework",
        "B. Overview",
        "1. Introduction",
        "3.3.1. Fine registration optimization based on IPSO",
        "REFERENCES",
        "Data availability",
        "CRediT authorship contribution statement",
        "2 Experiments",
        "引言",
    ]
    drop = [
        "1 N",
        "14 MODELS",
        "Fig. 3. The overall framework.",
        "TABLE II",
        "图 3 实验流程",
        "E = mc2 + 3",
        "Zhiyuan Li a, Fengxiang Jin a,b,*, Jian Wang a,b,*, Zhenyu Zhang a",
        "0.5",
        "a = b",
    ]
    for text in keep:
        check(f"保留 {text[:38]!r}", common.toc_entry_ok(text), "被误删")
    for text in drop:
        check(f"丢弃 {text[:38]!r}", not common.toc_entry_ok(text), "漏掉了")


def test_running_header() -> None:
    print("跨页重复的页眉")
    toc = [
        {"seq": 0, "text": "Synthetic Journal of Testing", "level": 1, "page": 1, "type": "heading"},
        {"seq": 1, "text": "A Synthetic Paper", "level": 0, "page": 1, "type": "title"},
        {"seq": 2, "text": "1. Introduction", "level": 1, "page": 1, "type": "heading"},
        {"seq": 3, "text": "Synthetic Journal of Testing 12 (2024) 345", "level": 1, "page": 2, "type": "heading"},
        {"seq": 4, "text": "2. Method", "level": 1, "page": 2, "type": "heading"},
        {"seq": 5, "text": "Synthetic Journal of Testing 12 (2024) 346", "level": 1, "page": 3, "type": "heading"},
        {"seq": 6, "text": "3. Results", "level": 1, "page": 3, "type": "heading"},
    ]
    kept = [entry["text"] for entry in common.filter_toc(toc)]
    check("期刊名（标题之前）被丢弃", "Synthetic Journal of Testing" not in kept, str(kept))
    check("逐页页码不同的页眉被丢弃", all("345" not in text and "346" not in text for text in kept), str(kept))
    check("正常章节保留", kept == ["A Synthetic Paper", "1. Introduction", "2. Method", "3. Results"], str(kept))

    # 页码放在行首 / 行尾都算同一行页眉（真实期刊两种写法混着出现）。
    mixed = [
        {"seq": 0, "text": "Evolutionary Multitasking Descriptor Optimization", "level": 0, "page": 1, "type": "title"},
        {"seq": 1, "text": "IEEE TRANSACTIONS ON EVOLUTIONARY COMPUTATION, VOL. 29, NO. 4, AUGUST 2025 1239", "level": 1, "page": 1, "type": "heading"},
        {"seq": 2, "text": "I. INTRODUCTION", "level": 1, "page": 1, "type": "heading"},
        {"seq": 3, "text": "1240 IEEE TRANSACTIONS ON EVOLUTIONARY COMPUTATION, VOL. 29, NO. 4, AUGUST 2025", "level": 1, "page": 2, "type": "heading"},
        {"seq": 4, "text": "II. RELATED WORK", "level": 1, "page": 2, "type": "heading"},
        {"seq": 5, "text": "1242 IEEE TRANSACTIONS ON EVOLUTIONARY COMPUTATION, VOL. 29, NO. 4, AUGUST 2025", "level": 1, "page": 4, "type": "heading"},
    ]
    kept_mixed = [entry["text"] for entry in common.filter_toc(mixed)]
    check(
        "页首/页尾页码混排的页眉能被认出",
        not any("IEEE TRANSACTIONS" in text for text in kept_mixed),
        str(kept_mixed),
    )

    # IEEE 那种「页码 + 刊名 + 卷期」的页眉：每页文字几乎一样，只有页码不同。
    ieee = [
        {"seq": 0, "text": "Evolutionary Multitasking Descriptor Optimization", "level": 0, "page": 1, "type": "title"},
        {"seq": 1, "text": "I. INTRODUCTION", "level": 1, "page": 1, "type": "heading"},
        {"seq": 2, "text": "1240 IEEE TRANSACTIONS ON EVOLUTIONARY COMPUTATION, VOL. 29, NO. 4, AUGUST 2025", "level": 1, "page": 2, "type": "heading"},
        {"seq": 3, "text": "II. RELATED WORK AND MOTIVATION", "level": 1, "page": 2, "type": "heading"},
        {"seq": 4, "text": "1242 IEEE TRANSACTIONS ON EVOLUTIONARY COMPUTATION, VOL. 29, NO. 4, AUGUST 2025", "level": 1, "page": 4, "type": "heading"},
        {"seq": 5, "text": "III. PROPOSED METHOD", "level": 1, "page": 4, "type": "heading"},
        {"seq": 6, "text": "1244 IEEE TRANSACTIONS ON EVOLUTIONARY COMPUTATION, VOL. 29, NO. 4, AUGUST 2025", "level": 1, "page": 6, "type": "heading"},
        {"seq": 7, "text": "14 MODELS", "level": 1, "page": 6, "type": "heading"},
        {"seq": 8, "text": "1 N", "level": 1, "page": 6, "type": "heading"},
    ]
    kept = [entry["text"] for entry in common.filter_toc(ieee)]
    check("IEEE 页眉被丢弃", not any("IEEE TRANSACTIONS" in text for text in kept), str(kept))
    check("图片刻度/公式残片被丢弃", "14 MODELS" not in kept and "1 N" not in kept, str(kept))
    check("IEEE 章节保留", kept == [
        "Evolutionary Multitasking Descriptor Optimization",
        "I. INTRODUCTION",
        "II. RELATED WORK AND MOTIVATION",
        "III. PROPOSED METHOD",
    ], str(kept))


# --------------------------------------------------------------- 合成 PDF 解析
def build_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    def text(x, y, content, size, bold=False):
        page.insert_text(
            pymupdf.Point(x, y),
            content,
            fontsize=size,
            fontname="hebo" if bold else "helv",
        )

    text(60, 18, "Synthetic Journal of Testing 12 (2024) 1243", 8)
    text(60, 40, "Synthetic Journal of Testing", 14)
    text(60, 80, "A Synthetic Paper for Parser Regression Tests", 20, bold=True)
    text(60, 104, "Alice Zhang a, Bob Li a,b,* , Carol Wu c, Dan Ma d", 10)
    text(60, 130, "Abstract. This synthetic document exists only to exercise the PDF parser.", 10)
    text(60, 170, "1. Introduction", 13, bold=True)
    text(60, 195, "Point cloud registration is a fundamental task in robotics and remote sensing.", 10)
    text(60, 215, "Existing methods struggle when the initial overlap is small.", 10)
    doc.new_page(width=595, height=842)
    page = doc[1]
    text(60, 30, "1244 Synthetic Journal of Testing 12 (2024) 345", 8)
    text(60, 70, "2. Method", 13, bold=True)
    text(60, 100, "Fig. 1. The overall pipeline of the proposed method, drawn as a block diagram.", 12, bold=True)
    text(60, 140, "A. Framework", 12, bold=True)
    text(60, 165, "The framework consists of three stages described in the following.", 10)
    text(180, 300, "14 MODELS", 11, bold=True)
    text(60, 340, "E = mc2 + 3", 10)
    doc.new_page(width=595, height=842)
    page = doc[2]
    text(60, 30, "1246 Synthetic Journal of Testing 12 (2024) 346", 8)
    text(60, 70, "B. Training Details", 12, bold=True)
    text(60, 100, "We train the model on four GPUs for two days.", 10)
    text(60, 140, "3. Results", 13, bold=True)
    text(60, 170, "The proposed method outperforms the baselines by a clear margin.", 10)
    text(60, 700, "References", 13, bold=True)
    text(60, 720, "[1] A. Author. A very important paper. In CVPR, 2024.", 9)
    doc.save(str(path))
    doc.close()


def test_parsed_toc() -> None:
    print("合成 PDF 的目录")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "synthetic.pdf"
        build_pdf(path)
        result = parse_pdf(path, "synthetic.pdf")
    toc = result["toc"]
    texts = [entry["text"] for entry in toc]
    for text in texts:
        print(f"    - {text[:70]}")
    check("标题在目录里", any("A Synthetic Paper" in text for text in texts), str(texts))
    check("正文标题在目录里", "1. Introduction" in texts and "2. Method" in texts, str(texts))
    check("单字章节 A. Framework 没被误删", "A. Framework" in texts, str(texts))
    check("页眉没进目录", not any("Synthetic Journal of Testing 1" in text for text in texts), str(texts))
    check(
        "首页顶部那行页眉也被改成 header",
        not any(block["type"] in ("heading", "paragraph") and "Synthetic Journal of Testing 12" in block["text"]
                for block in result["blocks"]),
        str([(b["type"], b["text"][:40]) for b in result["blocks"][:4]]),
    )
    check("图注没进目录", not any(text.lower().startswith("fig") for text in texts), str(texts))
    check("刻度 14 MODELS 没进目录", "14 MODELS" not in texts, str(texts))
    check("公式没进目录", not any("=" in text for text in texts), str(texts))
    check("作者行没进目录", not any("Alice Zhang" in text for text in texts), str(texts))
    check(
        "标题块是真正的论文标题",
        any(block["type"] == "title" and "A Synthetic Paper" in block["text"] for block in result["blocks"]),
        str([b["text"][:40] for b in result["blocks"] if b["type"] == "title"]),
    )
    check(
        "页眉块被标成 header",
        any(block["type"] == "header" for block in result["blocks"]),
        str([b["type"] for b in result["blocks"]]),
    )


def main() -> int:
    test_entry_rules()
    test_running_header()
    test_parsed_toc()
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败")
        for item in FAILURES:
            print(" -", item)
        return 1
    print("\n目录检查全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())