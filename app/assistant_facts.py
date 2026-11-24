"""AI 助手的「软件资料」：当前版本 + README 摘录 + 版本变化快照。

资料全部来自本机（打包进安装包的 README.md / RELEASES.md 与 config.APP_VERSION），
不联网、不入库，每次进程内只拼一次。

为什么不直接让模型上网读 Releases：ECPreader 对外承诺「数据不出电脑、无遥测」，
为了回答一个版本号而偷偷访问 GitHub 会破坏这个承诺，也会在断网/代理环境下变成
一个莫名其妙的等待。所以这里改用「随包发布的快照 + 当前版本号」，
并在提示词里要求模型对「有没有某个功能」这类问题点明版本。
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

from . import config

# README 里对用户有用的部分；下载表、打包、License 那些不提。
README_SECTIONS = ("功能", "使用", "数据与隐私", "反馈")
MAX_README_CHARS = 2600
MAX_RELEASES_CHARS = 1800
MAX_TOTAL_CHARS = 6200

CORE = """ECPreader（论文双语对照阅读器），Windows 10 / 11 桌面软件，Python + 内置浏览器窗口，本地运行、不需要联网账号。
作者：Treasure_moon。
反馈邮箱：treasure_moon@163.com（首选，报 bug 时附上版本号、论文类型和截图）。
开源仓库：https://github.com/Treasure-moon/ECPreader （README、Issues、Star 都在这里）。
下载与更新说明：https://github.com/Treasure-moon/ECPreader/releases/latest
安装形态：安装版 ECPreader-Setup-{version}.exe（可选安装路径和数据目录，可在「应用和功能」卸载），或免安装单文件 ECPReader.exe。
界面：上层顶栏放标签页 / 引擎状态 / ⋮ 设置入口，下层阅读栏放翻译、视图切换、缩放和查找；右侧是原文与译文两栏。
AI 阅读助手：右下角 ✦ 按钮（只有打开论文时出现，全屏阅读里同样可用，Esc 先关助手再退出全屏），悬浮窗里可以让它总结论文、解释方法与公式、找局限，也能问软件怎么用。
阅读助手读多少：默认只把论文前约 2.4 万字符（通常前几页）发过去，避免每次提问都烧很多额度；面板顶部会写明「只包含到第几页」。
可在「⋮ → 翻译设置 → AI 引擎 → 阅读助手上下文上限」调大（6000–160000 字符），或打开「总结全文时发送整篇」（默认关闭）。
取舍：上限翻倍，输入 token 与费用大致也翻倍；调大后能做整篇总结、跨章节问题，也不会漏掉后半部分。
设置入口：⋮ → 翻译设置（引擎与 API Key、术语表、主题与字号、数据与安全）；⋮ → 打开文档 / 历史记录 / 关于。查看版本号：顶栏 `?` 关于，或 ⋮ → 关于。
快捷键：Alt+T 新建标签、Alt+W 关闭当前标签、Alt+1…9 切到第 N 个标签、Ctrl+F 文档内查找、Esc 关查找栏或退出全屏、原版视图下 Ctrl+滚轮缩放。
数据：文档、译文、历史、API Key 都在本机数据目录；API Key 用 Windows DPAPI + AES-256-GCM 加密；无埋点、无遥测、无云端同步。
术语：原版对照＝整页按 PDF 原样渲染、译文按原坐标覆盖；逐句对照＝正文重排成左右两栏；免费引擎＝MyMemory / Google / Bing；AI 引擎＝任意 OpenAI 兼容接口。"""


def _search_dirs() -> list[Path]:
    """打包后资源在 _MEIPASS，源码运行则在仓库根目录。"""
    dirs: list[Path] = []
    bundled = getattr(sys, "_MEIPASS", "")
    if bundled:
        dirs.append(Path(bundled))
    dirs.append(Path(config.BASE_DIR))
    dirs.append(Path(__file__).resolve().parent.parent)
    return dirs


def _read_doc(name: str) -> str:
    for directory in _search_dirs():
        target = directory / name
        try:
            if target.is_file():
                return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return ""


def _versions() -> list[tuple[str, bool]]:
    """从 RELEASES.md 的 `## x.y.z（开发版）` 标题里读出版本号与是否已发布。"""
    text = _read_doc("RELEASES.md")
    found: list[tuple[str, bool]] = []
    for match in re.finditer(r"(?m)^##\s+(\d+\.\d+\.\d+)([^\n]*)$", text):
        found.append((match.group(1), "开发版" in match.group(2) or "未发布" in match.group(2)))
    return found


def _version_lines() -> str:
    """讲清楚「你现在跑的版本」，但绝不替用户判断是不是最新版。

    这份资料随程序打包、不联网，天然可能过期；「有没有新版」只能让用户去
    Releases 自己看，所以这里同时给出链接与措辞。
    """
    current = config.APP_VERSION
    versions = _versions()
    dev = any(version == current and is_dev for version, is_dev in versions)
    published = next((version for version, is_dev in versions if not is_dev), "")
    lines = [f"当前运行版本：ECPreader {current}" + ("（开发版／源码版，功能已经可用）。" if dev else "。")]
    if dev and published:
        lines.append(
            f"本地资料里记录的最近发布安装包是 {published}：{current} 的新功能在 {published} 安装包中还没有。"
        )
    lines.append(
        "打包进程序的这份资料本身不会更新；如果资料最后还附了「联网查到的最新发布」，"
        "就以那一条为准回答是否最新；没有附上时让用户到 "
        "https://github.com/Treasure-moon/ECPreader/releases 自己看，并说明他当前的版本号。"
    )
    return "\n".join(lines)


def _trim(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return cut.rsplit("\n", 1)[0].rstrip() + "\n……（后面还有内容，这里省略）"


def readme_excerpt() -> str:
    """只保留「功能 / 使用 / 数据与隐私 / 反馈」几节。"""
    text = _read_doc("README.md")
    if not text:
        return ""
    blocks = re.split(r"(?m)^##\s+", text)
    kept: list[str] = []
    intro = blocks[0] if blocks else ""
    intro = re.sub(r"<[^>]+>", "", intro)  # 去掉 logo 之类的 HTML
    if intro.strip():
        kept.append(intro.strip())
    for block in blocks[1:]:
        title, _, body = block.partition("\n")
        if any(key in title for key in README_SECTIONS):
            kept.append(f"## {title.strip()}\n{body.strip()}")
    return _trim("\n\n".join(kept), MAX_README_CHARS)


def releases_excerpt() -> str:
    text = _read_doc("RELEASES.md")
    if not text:
        return ""
    body = re.sub(r"(?m)^#\s+.*$", "", text, count=1).strip()
    return _trim(body, MAX_RELEASES_CHARS)


@lru_cache(maxsize=1)
def build_facts() -> str:
    """拼出「=== 软件资料 ===」那段文本（当前版本号永远来自 config）。"""
    parts = [_version_lines(), CORE.format(version=config.APP_VERSION)]
    readme = readme_excerpt()
    if readme:
        parts.append("【README 摘录】\n" + readme)
    releases = releases_excerpt()
    if releases:
        parts.append("【版本变化快照】（当前版本是 " + config.APP_VERSION + "，更早版本只说「已在某版本加入」）\n" + releases)
    return _trim("\n\n".join(parts), MAX_TOTAL_CHARS)