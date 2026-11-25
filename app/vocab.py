"""生词本：收藏的不是「单词」，而是「这个单词的某个语义」。

为什么这样设计：同一个词在不同论文里常常是不同义项（bank = 银行／河岸／存储），
只记单词会让复习时看到一堆分不清的条目。所以每条收藏都同时记住：

- sense：这次用它的是哪个常见直译（生词本里按它分组）
- gloss：AI 给出的语境释义（同一个直译下，不同文章的说法可能更细）
- doc/page/seq：来源文章与位置，能跳回去看原句
- category：用户自己的分类

判断「这个词的这个语义收藏过没有」用 sem_key：有语境释义就用语境释义，
没有就退回直译（纯机翻模式下拿不到更细的东西）。
"""
from __future__ import annotations

import html as _html
import re
import uuid
from typing import Any

from . import db

# 直译串里常见的分隔符：机翻/词典经常返回「银行，河岸；岸」这种
SPLIT = re.compile(r"[；;，,、/|｜]+")
# 归一化时去掉的标点与空白，用来比较两个说法是不是同一个语义
NOISE = re.compile(r"[\s，,、；;。.：:·\-—_()（）\[\]【】\"'“”‘’]+")
POS_PREFIX = re.compile(r"^(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|int|phr|abbr)\.\s*", re.I)
# 机翻（Google/Bing/MyMemory）会把词条包在 <g id="1">…</g>、<b>…</b> 里返回
TAG = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9]*(?:\s[^<>]{0,60})?\s*/?>")
# 标签被分隔符切断后会剩下 "<g id=\"2\"" / "g>" 这种残片
BROKEN_TAG = re.compile(r"<\s*/?\s*[A-Za-z][^<>]{0,20}>?", re.I)
# 义项末尾的句读与符号
TAIL = re.compile(r"[\s。．.!！?？,，;；:：、·…~\-—*#`]+$")
INNER_PUNCT = re.compile(r"[。．.!！?？;；]")
MARKDOWN = re.compile(r"[*#`]+")
# 标签被截断后剩下的尖括号（「得到的 <」）
ANGLE = re.compile(r"[<>]+")
WRAP = " 	　" + "\"'“”‘’《》"
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def norm(text: Any) -> str:
    return NOISE.sub("", str(text or "")).strip().lower()


def strip_markup(text: Any) -> str:
    """只去掉机翻的 HTML 标记与实体，保留标点（拆义项前用）。"""
    value = str(text or "")
    value = TAG.sub(" ", value)
    value = BROKEN_TAG.sub(" ", value)
    return _html.unescape(value)


def tidy(text: Any) -> str:
    """去掉机翻/模型带回来的标记与实体，压平空白。

    Google 免费接口会把片段包成 `<g id="1">译文</g>`，被标点切开后还会剩下
    `g>`、`<g id="2"` 这种残片；这些都不该出现在「常见义项」里。
    """
    return " ".join(strip_markup(text).split()).strip()


def usable_sense(text: Any) -> bool:
    """义项得像个词义：有汉字，或至少是三个字母以上的英文。"""
    item = str(text or "")
    if HAN.search(item):
        return True
    return bool(re.search(r"[A-Za-z]{3,}", item))


def clean_sense(text: Any) -> str:
    # 义项是短词条：句读换成空格、去掉首尾引号与标点；数字（如 0.5）保持原样
    value = MARKDOWN.sub(" ", POS_PREFIX.sub("", tidy(text)))
    value = ANGLE.sub(" ", value)
    value = INNER_PUNCT.sub(" ", value).strip(WRAP)
    value = TAIL.sub("", value).strip(WRAP)
    return " ".join(value.split())


def split_senses(word_zh: Any, limit: int = 6) -> list[str]:
    """把「银行，河岸」这种直译拆成多个义项，保持出现顺序、去重。"""
    parts: list[str] = []
    seen: set[str] = set()
    for piece in SPLIT.split(strip_markup(word_zh)):
        item = clean_sense(piece)
        if not item or not usable_sense(item):
            continue
        key = norm(item)
        if not key or key in seen:
            continue
        seen.add(key)
        parts.append(item)
        if len(parts) >= limit:
            break
    return parts


def sem_key(sense: str, gloss: str) -> str:
    return norm(gloss) or norm(sense)


def _row(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    item.pop("sense_key", None)
    item.pop("sem_key", None)
    return item


def _doc_alive(doc_id: str | None) -> bool:
    if not doc_id:
        return False
    return db.get_document_meta(doc_id) is not None


# ------------------------------------------------------------------ 词条状态
def word_entries(word: str) -> list[dict[str, Any]]:
    key = norm(word)
    if not key:
        return []
    conn = db._connect()
    with db._lock:
        rows = conn.execute(
            "SELECT * FROM vocab_entries WHERE word=? ORDER BY created_at DESC, rowid DESC",
            (key,),
        ).fetchall()
    entries = [_row(dict(row)) for row in rows]
    for entry in entries:
        entry["alive"] = _doc_alive(entry.get("doc_id"))
    return entries


def word_senses(word: str) -> list[str]:
    key = norm(word)
    conn = db._connect()
    with db._lock:
        row = conn.execute("SELECT senses_json FROM vocab_words WHERE word=?", (key,)).fetchone()
    if not row:
        return []
    items = [clean_sense(item) for item in db._load(row["senses_json"], [])]
    return [item for item in dict.fromkeys(item for item in items if usable_sense(item))]


def _same_sense(entry: dict[str, Any], sense: str = "", gloss: str = "") -> bool:
    """判断两个义项是不是同一个语义。

    收藏时语境示意（gloss）优先，词典直译（sense）兜底；调用方只给其中一项时
    也要能对上已经存下的另一项，否则「已收藏」会判成没收藏。
    """
    wanted = {norm(sense), norm(gloss)}
    wanted.discard("")
    if not wanted:
        return False
    stored = {
        norm(entry.get("sense")),
        norm(entry.get("gloss")),
        str(entry.get("sense_key") or ""),
        str(entry.get("sem_key") or ""),
    }
    stored.discard("")
    return bool(wanted & stored)


def status(word: str, sense: str = "", gloss: str = "") -> dict[str, Any]:
    """给查词浮层用：这个词一共收藏了几个语义、当前这个语义收藏了没有。"""
    key = norm(word)
    key_sem = sem_key(sense, gloss)
    entries = word_entries(key)
    senses = {str(item.get("sense") or "") for item in entries}
    kept = [item for item in entries if _same_sense(item, sense, gloss)]
    return {
        "word": key,
        "display": (entries[0].get("display") if entries else word) or word,
        "count": len({sem_key(item.get("sense", ""), item.get("gloss", "")) for item in entries}) if entries else 0,
        "sense_count": len(senses) if entries else 0,
        "collected": bool(kept) and bool(key_sem or norm(sense)),
        "entry_ids": [item["id"] for item in kept],
        "matches": kept,
        "senses": word_senses(key),
        "entries": entries[:20],
        "categories": list_categories(),
    }


# ------------------------------------------------------------------ 收藏 / 取消
def collect(
    *,
    word: str,
    display: str = "",
    sense: str = "",
    gloss: str = "",
    pos: str = "",
    phonetic: str = "",
    senses: list[str] | None = None,
    doc_id: str | None = None,
    doc_title: str = "",
    page: int | None = None,
    seq: int | None = None,
    sentence: str = "",
    sentence_zh: str = "",
    category_id: str | None = None,
) -> dict[str, Any]:
    key = norm(word)
    if not key:
        raise ValueError("没有有效的单词")
    gloss_text = " ".join(tidy(gloss).split())
    raw_sense = str(sense or "")
    sense_text = clean_sense(raw_sense) or clean_sense(gloss_text)
    # 直译是机翻标记碎片（「<g id="1"> 得到的。<」）时，语境示意更可信
    if re.search(r"[<>]", raw_sense) and usable_sense(clean_sense(gloss_text)):
        sense_text = clean_sense(gloss_text)
    key_sense = norm(sense_text)
    key_sem = sem_key(sense_text, gloss_text)

    # 同一个词的所有义项一起记住，生词本里才能把没收藏的常见义项也列出来；
    # 带 <g id="1"> 标记的那些是拆机翻串拆出来的垃圾，不要存
    clean_list = [
        clean_sense(item) for item in (senses or []) if not re.search(r"[<>]", str(item or ""))
    ]
    merged = list(dict.fromkeys([*clean_list, sense_text]))
    merged = [item for item in merged if usable_sense(item)]
    merged = [item for item in dict.fromkeys([*word_senses(key), *merged]) if item]
    ts = db.now()
    conn = db._connect()
    with db._lock:
        conn.execute(
            "INSERT INTO vocab_words (word,display,phonetic,pos,senses_json,updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(word) DO UPDATE SET "
            "display=excluded.display, phonetic=CASE WHEN excluded.phonetic<>'' THEN excluded.phonetic ELSE vocab_words.phonetic END,"
            "pos=CASE WHEN excluded.pos<>'' THEN excluded.pos ELSE vocab_words.pos END,"
            "senses_json=excluded.senses_json, updated_at=excluded.updated_at",
            (key, display or word, phonetic, pos, db._dump(merged), ts),
        )
        rows = conn.execute(
            "SELECT * FROM vocab_entries WHERE word=? AND IFNULL(doc_id,'')=?",
            (key, doc_id or ""),
        ).fetchall()
        existing = next((row for row in rows if _same_sense(dict(row), sense_text, gloss_text)), None)
        if existing:
            entry_id = existing["id"]
            # 老行只有直译、这次拿到了语境示意：补上，之后按语义匹配才准
            if gloss_text and not str(existing["gloss"] or "").strip():
                conn.execute(
                    "UPDATE vocab_entries SET gloss=?, sem_key=?, sense_key=?, sense=? WHERE id=?",
                    (gloss_text, key_sem, key_sense, sense_text or existing["sense"], entry_id),
                )
            conn.commit()
        else:
            entry_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO vocab_entries (id,word,display,sense,sense_key,sem_key,gloss,phonetic,pos,"
                "category_id,doc_id,doc_title,page,seq,sentence,sentence_zh,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry_id, key, display or word, sense_text, key_sense, key_sem, gloss_text,
                    phonetic, pos, category_id, doc_id, db.clean_title(doc_title), page, seq,
                    str(sentence or "")[:1200], str(sentence_zh or "")[:1200], ts,
                ),
            )
            conn.commit()
    return status(key, sense_text, gloss_text)


def remove_sense(word: str, sense: str = "", gloss: str = "") -> int:
    """取消收藏「这个语义」：可能来自多篇文章，一起删掉。"""
    key = norm(word)
    if not key or not (norm(sense) or norm(gloss)):
        return 0
    conn = db._connect()
    with db._lock:
        rows = conn.execute("SELECT * FROM vocab_entries WHERE word=?", (key,)).fetchall()
        ids = [row["id"] for row in rows if _same_sense(dict(row), sense, gloss)]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = conn.execute(f"DELETE FROM vocab_entries WHERE id IN ({placeholders})", ids)
        conn.commit()
    return int(cursor.rowcount or 0)


def delete_entry(entry_id: str) -> bool:
    conn = db._connect()
    with db._lock:
        cursor = conn.execute("DELETE FROM vocab_entries WHERE id=?", (entry_id,))
        conn.commit()
    return bool(cursor.rowcount)


def set_category(entry_id: str, category_id: str | None) -> bool:
    conn = db._connect()
    with db._lock:
        cursor = conn.execute(
            "UPDATE vocab_entries SET category_id=? WHERE id=?",
            (category_id or None, entry_id),
        )
        conn.commit()
    return bool(cursor.rowcount)


# ------------------------------------------------------------------ 分类
def list_categories() -> list[dict[str, Any]]:
    conn = db._connect()
    with db._lock:
        rows = conn.execute(
            "SELECT c.id, c.name, c.created_at, COUNT(e.id) AS total "
            "FROM vocab_categories c LEFT JOIN vocab_entries e ON e.category_id=c.id "
            "GROUP BY c.id ORDER BY c.created_at, c.rowid"
        ).fetchall()
    return [dict(row) for row in rows]


def create_category(name: str) -> dict[str, Any]:
    title = " ".join(str(name or "").split())[:40]
    if not title:
        raise ValueError("分类名不能为空")
    conn = db._connect()
    with db._lock:
        row = conn.execute("SELECT * FROM vocab_categories WHERE name=?", (title,)).fetchone()
        if row:
            return dict(row)
        category_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO vocab_categories (id,name,created_at) VALUES (?,?,?)",
            (category_id, title, db.now()),
        )
        conn.commit()
    return {"id": category_id, "name": title, "total": 0}


def assign_category(entry_ids: list[str], category_id: str | None) -> int:
    """整篇/整批改分类：按文章视图里把一篇文章的收藏一次拖到某个分类。"""
    ids = [str(item) for item in (entry_ids or []) if str(item or "").strip()]
    if not ids:
        return 0
    target = str(category_id or "").strip() or None
    conn = db._connect()
    with db._lock:
        if target and not conn.execute(
            "SELECT 1 FROM vocab_categories WHERE id=?", (target,)
        ).fetchone():
            raise ValueError("分类不存在")
        marks = ",".join("?" for _ in ids)
        cursor = conn.execute(
            f"UPDATE vocab_entries SET category_id=? WHERE id IN ({marks})",
            [target, *ids],
        )
        conn.commit()
    return int(cursor.rowcount or 0)


def delete_category(category_id: str) -> bool:
    conn = db._connect()
    with db._lock:
        conn.execute("UPDATE vocab_entries SET category_id=NULL WHERE category_id=?", (category_id,))
        cursor = conn.execute("DELETE FROM vocab_categories WHERE id=?", (category_id,))
        conn.commit()
    return bool(cursor.rowcount)


def rename_category(category_id: str, name: str) -> bool:
    title = " ".join(str(name or "").split())[:40]
    if not title:
        return False
    conn = db._connect()
    with db._lock:
        try:
            cursor = conn.execute(
                "UPDATE vocab_categories SET name=? WHERE id=?", (title, category_id)
            )
            conn.commit()
        except Exception:
            conn.rollback()
            return False
    return bool(cursor.rowcount)


# ------------------------------------------------------------------ 列表 / 导出
def overview() -> dict[str, Any]:
    """生词本整页数据：按词归组，每组带上已收藏的义项与来源。"""
    conn = db._connect()
    with db._lock:
        rows = conn.execute(
            "SELECT * FROM vocab_entries ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        words = {row["word"]: dict(row) for row in conn.execute("SELECT * FROM vocab_words").fetchall()}
        categories = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM vocab_categories").fetchall()}
    entries = [_row(dict(row)) for row in rows]
    alive: dict[str, bool] = {}
    for entry in entries:
        doc_id = entry.get("doc_id")
        if doc_id and doc_id not in alive:
            alive[doc_id] = db.get_document_meta(doc_id) is not None
        entry["alive"] = alive.get(doc_id, False) if doc_id else False
        entry["category_name"] = (categories.get(entry.get("category_id") or "") or {}).get("name", "")

    grouped: dict[str, dict[str, Any]] = {}
    for entry in entries:
        word = entry["word"]
        bucket = grouped.get(word)
        if bucket is None:
            meta = words.get(word, {})
            bucket = grouped[word] = {
                "word": word,
                "display": entry.get("display") or meta.get("display") or word,
                "phonetic": meta.get("phonetic") or entry.get("phonetic") or "",
                "pos": meta.get("pos") or entry.get("pos") or "",
                "senses": [str(item) for item in db._load(meta.get("senses_json"), []) if str(item).strip()],
                "entries": [],
            }
        bucket["entries"].append(entry)

    for bucket in grouped.values():
        collected = {norm(item.get("sense")) for item in bucket["entries"]}
        # 收藏了不常见的义项时也要列出来并重点标记，所以补进义项表
        for item in bucket["entries"]:
            sense = clean_sense(item.get("sense"))
            if sense and norm(sense) not in {norm(x) for x in bucket["senses"]}:
                bucket["senses"].append(sense)
        bucket["collected_senses"] = sorted(collected)
        bucket["senses"] = [item for item in dict.fromkeys(bucket["senses"]) if item]
    # 按单词查看时按字典序：a、b、c…，大小写不敏感
    ordered = sorted(
        grouped.values(),
        key=lambda item: (str(item.get("display") or item["word"]).casefold(), item["word"]),
    )
    return {
        "words": ordered,
        "total_words": len(grouped),
        "total_entries": len(entries),
        "total_senses": len({(entry["word"], sem_key(entry.get("sense", ""), entry.get("gloss", ""))) for entry in entries}),
        "categories": list_categories(),
    }


def sanitize_stored() -> int:
    """把历史收藏里混进机翻标记的义项洗一遍（幂等，启动时跑一次）。"""
    conn = db._connect()
    changed = 0
    with db._lock:
        for row in conn.execute("SELECT id, sense, gloss FROM vocab_entries").fetchall():
            gloss = " ".join(tidy(row["gloss"]).split())
            sense = clean_sense(row["sense"])
            dirty = bool(re.search(r"[<>]", str(row["sense"] or "")))
            if dirty and usable_sense(clean_sense(gloss)):
                # 直译是「<g id="1"> 得到的。<」这种机翻残片，用语境示意顶替
                sense = clean_sense(gloss)
            elif not usable_sense(sense):
                sense = clean_sense(gloss)
            if sense == (row["sense"] or "") and gloss == (row["gloss"] or ""):
                continue
            conn.execute(
                "UPDATE vocab_entries SET sense=?, sense_key=? WHERE id=?",
                (sense, norm(sense), row["id"]),
            )
            changed += 1
        for row in conn.execute("SELECT word, senses_json FROM vocab_words").fetchall():
            items = db._load(row["senses_json"], [])
            # 带标记的义项是拆机翻串拆出来的垃圾，直接丢掉，不要洗成半截中文
            kept = [item for item in items if not re.search(r"[<>]", str(item or ""))]
            cleaned = [clean_sense(item) for item in kept]
            cleaned = [item for item in dict.fromkeys(cleaned) if usable_sense(item)]
            if cleaned != items:
                conn.execute(
                    "UPDATE vocab_words SET senses_json=? WHERE word=?",
                    (db._dump(cleaned), row["word"]),
                )
                changed += 1
        # 带机翻标记的查词缓存也清掉，下次查同一个词会重新拿干净结果
        purged = conn.execute(
            "DELETE FROM dict_cache WHERE data_json LIKE '%<g %' OR data_json LIKE '%</g>%'"
        ).rowcount
        if changed or purged:
            conn.commit()
    return changed + max(purged, 0)


def export_rows() -> list[dict[str, Any]]:
    data = overview()
    rows: list[dict[str, Any]] = []
    for bucket in data["words"]:
        for entry in bucket["entries"]:
            rows.append(
                {
                    "单词": bucket["display"],
                    "音标": bucket["phonetic"],
                    "直译": entry.get("sense", ""),
                    "语境示意": entry.get("gloss", ""),
                    "分类": entry.get("category_name", ""),
                    "来源文章": entry.get("doc_title", ""),
                    "页码": entry.get("page") if entry.get("page") is not None else "",
                    "原句": entry.get("sentence", ""),
                    "原句译文": entry.get("sentence_zh", ""),
                    "收藏时间": entry.get("created_at", ""),
                    "文章状态": "正常" if entry.get("alive") else "文章丢失",
                }
            )
    return rows


def clear() -> None:
    conn = db._connect()
    with db._lock:
        conn.execute("DELETE FROM vocab_entries")
        conn.execute("DELETE FROM vocab_words")
        conn.commit()


# ------------------------------------------------------------------ 导出
def to_csv() -> bytes:
    import csv
    import io

    rows = export_rows()
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        buffer.write("单词,音标,直译,语境示意,分类,来源文章,页码,原句,原句译文,收藏时间,文章状态\n")
    # Excel 默认按 GBK 打开无 BOM 的 UTF-8 会乱码，加 BOM 最省事
    return buffer.getvalue().encode("utf-8-sig")


def to_markdown() -> str:
    data = overview()
    lines = [f"# 生词本（{db.now()[:10]}）", ""]
    if not data["words"]:
        lines.append("（还是空的）")
    for bucket in data["words"]:
        title = bucket["display"]
        if bucket.get("phonetic"):
            title += " " + str(bucket["phonetic"])
        lines.append(f"## {title}")
        collected: dict[str, list[dict[str, Any]]] = {}
        for entry in bucket["entries"]:
            collected.setdefault(clean_sense(entry.get("sense")), []).append(entry)
        for sense in bucket["senses"]:
            items = collected.get(sense) or []
            if items:
                lines.append(f"- **{sense}**")
                for entry in items:
                    gloss = f"：{entry['gloss']}" if entry.get("gloss") else ""
                    source = entry.get("doc_title") or "（无来源）"
                    if not entry.get("alive"):
                        source += "（文章丢失）"
                    page = f" 第 {entry['page']} 页" if entry.get("page") else ""
                    lines.append(
                        f"  - {entry.get('category_name') or '未分类'}{gloss} —— 《{source}》{page} · {entry.get('created_at', '')}"
                    )
            else:
                lines.append(f"- {sense}")
        lines.append("")
    return "\n".join(lines)