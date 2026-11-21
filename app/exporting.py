"""导出：双语 Markdown / HTML / 备份 JSON。"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Any


def _segment_texts(block: dict[str, Any]) -> tuple[list[str], list[str]]:
    translation = block.get("translation") or {}
    segments = translation.get("segments") or {}
    source: list[str] = []
    target: list[str] = []
    if block.get("type") == "table":
        for row in block.get("cells") or []:
            for cell in row:
                source.append(cell["src"])
                target.append(segments.get(cell["sid"], ""))
    else:
        for segment in block.get("segments") or []:
            source.append(segment["src"])
            target.append(segments.get(segment["sid"], ""))
    return source, target


def to_markdown(document: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# {document.get('title') or '未命名文档'}",
        "",
        f"> ECPreader 双语对照导出 · {datetime.now():%Y-%m-%d %H:%M} · "
        f"原文 {document.get('filename', '')}",
        "",
        "---",
        "",
    ]
    for block in document.get("blocks") or []:
        block_type = block.get("type")
        if block_type == "table":
            rows = block.get("cells") or []
            segments = (block.get("translation") or {}).get("segments") or {}
            if rows:
                lines.append("| " + " | ".join(cell["src"] for cell in rows[0]) + " |")
                lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
                for row in rows[1:]:
                    lines.append("| " + " | ".join(cell["src"] for cell in row) + " |")
                lines.append("")
                if any(segments.get(cell["sid"]) for row in rows for cell in row):
                    lines.append("**译表**")
                    lines.append("")
                    lines.append("| " + " | ".join(segments.get(cell["sid"], "") or " " for cell in rows[0]) + " |")
                    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
                    for row in rows[1:]:
                        lines.append("| " + " | ".join(segments.get(cell["sid"], "") or " " for cell in row) + " |")
                    lines.append("")
            continue
        source, target = _segment_texts(block)
        if block_type in ("heading", "title"):
            lines.append(f"## {' '.join(source)}")
            if any(target):
                lines.append(f"### {' '.join(target)}")
            lines.append("")
            continue
        if block_type == "formula":
            lines.append(f"```\n{''.join(source)}\n```")
            lines.append("")
            continue
        if block_type == "caption":
            lines.append(f"**{' '.join(source)}**")
            if any(target):
                lines.append(f"**{' '.join(target)}**")
            lines.append("")
            continue
        lines.append(" ".join(source).strip())
        lines.append("")
        translated = " ".join(part for part in target if part).strip()
        if translated:
            lines.append(f"> {translated}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def to_html(document: dict[str, Any]) -> str:
    title = html.escape(document.get("title") or "未命名文档")
    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        f"<title>{title}</title>",
        "<style>",
        "body{max-width:1100px;margin:40px auto;padding:0 24px;font-family:Georgia,'Times New Roman','Songti SC',serif;"
        "background:#faf7f0;color:#1f2328;line-height:1.75}",
        ".row{display:grid;grid-template-columns:1fr 1fr;gap:28px;border-bottom:1px solid #e6e1d6;padding:14px 0}",
        ".en{color:#333}.zh{color:#1c3d35}",
        "h1{font-size:26px}h2{font-size:20px;margin:28px 0 6px}",
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #d9d2c4;padding:6px 8px;font-size:14px}",
        "blockquote{margin:6px 0;padding-left:12px;border-left:3px solid #b9cbc3;color:#1c3d35}",
        ".meta{color:#8a8172;font-size:13px;margin-bottom:28px}",
        "</style></head><body>",
        f"<h1>{title}</h1>",
        f'<div class="meta">ECPreader 双语对照导出 · {datetime.now():%Y-%m-%d %H:%M}</div>',
    ]
    for block in document.get("blocks") or []:
        block_type = block.get("type")
        if block_type == "table":
            rows = block.get("cells") or []
            segments = (block.get("translation") or {}).get("segments") or {}
            parts.append("<table>")
            for row in rows:
                cells = []
                for cell in row:
                    zh = segments.get(cell["sid"], "")
                    cells.append(
                        f'<td><div class="en">{html.escape(cell["src"])}</div>'
                        + (f'<div class="zh">{html.escape(zh)}</div>' if zh else "")
                        + "</td>"
                    )
                parts.append("<tr>" + "".join(cells) + "</tr>")
            parts.append("</table>")
            continue
        source, target = _segment_texts(block)
        source_text = html.escape(" ".join(source))
        target_text = html.escape(" ".join(part for part in target if part))
        if block_type in ("heading", "title"):
            parts.append(f"<h2>{source_text}</h2>")
            if target_text:
                parts.append(f"<p><strong>{target_text}</strong></p>")
            continue
        if block_type == "formula":
            parts.append(f'<pre class="formula">{source_text}</pre>')
            continue
        parts.append('<div class="row">')
        parts.append(f'<div class="en">{source_text}</div>')
        parts.append(f'<div class="zh">{target_text}</div>')
        parts.append("</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


def to_plain(document: dict[str, Any]) -> str:
    return "\n\n".join(
        (block.get("text") or "")
        for block in document.get("blocks") or []
        if block.get("type") != "table"
    )