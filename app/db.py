"""SQLite 本地存储：文档、段落译文、翻译历史、设置、词典缓存。"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Iterable

from . import config

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            config.ensure_dirs()
            _conn = sqlite3.connect(
                str(config.DB_FILE), check_same_thread=False, timeout=15
            )
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _init(_conn)
        return _conn


def checkpoint() -> None:
    """把 WAL 写回主库，供数据目录迁移时安全复制。"""
    with _lock:
        if _conn is not None:
            _conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            _conn.commit()


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            filename TEXT NOT NULL,
            filetype TEXT NOT NULL,
            page_count INTEGER,
            status TEXT NOT NULL DEFAULT 'parsed',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}',
            toc_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS blocks (
            doc_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            data_json TEXT NOT NULL,
            tr_json TEXT,
            PRIMARY KEY (doc_id, seq)
        );
        CREATE TABLE IF NOT EXISTS history (
            id TEXT PRIMARY KEY,
            doc_id TEXT,
            title TEXT NOT NULL,
            mode TEXT,
            engine TEXT,
            model TEXT,
            status TEXT NOT NULL,
            block_total INTEGER DEFAULT 0,
            block_done INTEGER DEFAULT 0,
            char_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            finished_at TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dict_cache (
            word TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            data_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (word, context_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at);
        """
    )
    conn.commit()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


# ------------------------------------------------------------------ documents
def create_document(
    *,
    title: str,
    filename: str,
    filetype: str,
    page_count: int | None,
    blocks: list[dict[str, Any]],
    toc: list[dict[str, Any]],
    meta: dict[str, Any],
) -> str:
    doc_id = uuid.uuid4().hex
    ts = now()
    conn = _connect()
    with _lock:
        conn.execute(
            "INSERT INTO documents (id,title,filename,filetype,page_count,status,"
            "created_at,updated_at,meta_json,toc_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                title,
                filename,
                filetype,
                page_count,
                "parsed",
                ts,
                ts,
                _dump(meta),
                _dump(toc),
            ),
        )
        conn.executemany(
            "INSERT INTO blocks (doc_id,seq,data_json,tr_json) VALUES (?,?,?,NULL)",
            [(doc_id, seq, _dump(block)) for seq, block in enumerate(blocks)],
        )
        conn.commit()
    return doc_id


def _block_key(block: dict[str, Any]) -> str:
    if block.get("type") == "table":
        rows = block.get("cells") or []
        return "table|" + "|".join(" ".join(str(cell) for cell in row) for row in rows).lower()
    return "text|" + " ".join(str(block.get("text") or "").split()).lower()


def replace_blocks(
    doc_id: str,
    *,
    blocks: list[dict[str, Any]],
    toc: list[dict[str, Any]],
    page_count: int | None,
    meta: dict[str, Any],
    title: str | None = None,
) -> int:
    """重新解析后替换段落，并按原文文本把已有译文搬过去，返回保住的译文数。"""
    conn = _connect()
    with _lock:
        rows = conn.execute(
            "SELECT seq,data_json,tr_json FROM blocks WHERE doc_id=? ORDER BY seq",
            (doc_id,),
        ).fetchall()
        old_translations: dict[str, str] = {}
        for row in rows:
            raw = row["tr_json"]
            if not raw:
                continue
            try:
                data = json.loads(row["data_json"])
            except json.JSONDecodeError:
                continue
            old_translations[_block_key(data)] = raw
        conn.execute("DELETE FROM blocks WHERE doc_id=?", (doc_id,))
        kept = 0
        payload = []
        for seq, block in enumerate(blocks):
            key = _block_key(block)
            translation = old_translations.get(key)
            if translation:
                kept += 1
            payload.append((doc_id, seq, _dump(block), translation))
        conn.executemany(
            "INSERT INTO blocks (doc_id,seq,data_json,tr_json) VALUES (?,?,?,?)", payload
        )
        if title:
            conn.execute(
                "UPDATE documents SET title=?, page_count=?, meta_json=?, toc_json=?, updated_at=? WHERE id=?",
                (title, page_count, _dump(meta), _dump(toc), now(), doc_id),
            )
        else:
            conn.execute(
                "UPDATE documents SET page_count=?, meta_json=?, toc_json=?, updated_at=? WHERE id=?",
                (page_count, _dump(meta), _dump(toc), now(), doc_id),
            )
        conn.commit()
    return kept


def get_document_meta(doc_id: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["meta"] = _load(data.pop("meta_json"), {})
    data["toc"] = _load(data.pop("toc_json"), [])
    return data


def get_document(doc_id: str, *, with_blocks: bool = True) -> dict[str, Any] | None:
    meta = get_document_meta(doc_id)
    if meta is None:
        return None
    if with_blocks:
        conn = _connect()
        with _lock:
            rows = conn.execute(
                "SELECT seq,data_json,tr_json FROM blocks WHERE doc_id=? ORDER BY seq",
                (doc_id,),
            ).fetchall()
        blocks = []
        for row in rows:
            block = _load(row["data_json"], {})
            block["seq"] = row["seq"]
            tr = _load(row["tr_json"], None)
            block["translation"] = tr
            blocks.append(block)
        meta["blocks"] = blocks
    return meta


def list_documents(limit: int = 50, query: str = "") -> list[dict[str, Any]]:
    conn = _connect()
    sql = (
        "SELECT id,title,filename,filetype,page_count,status,created_at,updated_at "
        "FROM documents"
    )
    params: list[Any] = []
    if query:
        sql += " WHERE title LIKE ? OR filename LIKE ?"
        like = f"%{query}%"
        params += [like, like]
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _lock:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: str) -> None:
    conn = _connect()
    with _lock:
        conn.execute("DELETE FROM blocks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        conn.execute("DELETE FROM history WHERE doc_id=?", (doc_id,))
        conn.commit()


def update_document_status(doc_id: str, status: str) -> None:
    conn = _connect()
    with _lock:
        conn.execute(
            "UPDATE documents SET status=?, updated_at=? WHERE id=?",
            (status, now(), doc_id),
        )
        conn.commit()


def save_block_translation(doc_id: str, seq: int, translation: dict[str, Any]) -> None:
    conn = _connect()
    with _lock:
        conn.execute(
            "UPDATE blocks SET tr_json=? WHERE doc_id=? AND seq=?",
            (_dump(translation), doc_id, seq),
        )
        conn.commit()


def save_block_translations(
    doc_id: str, items: Iterable[tuple[int, dict[str, Any]]]
) -> None:
    conn = _connect()
    with _lock:
        conn.executemany(
            "UPDATE blocks SET tr_json=? WHERE doc_id=? AND seq=?",
            [(_dump(tr), doc_id, seq) for seq, tr in items],
        )
        conn.commit()


# -------------------------------------------------------------------- history
def add_history(
    *,
    doc_id: str,
    title: str,
    mode: str,
    engine: str,
    model: str,
    status: str,
    block_total: int = 0,
    block_done: int = 0,
    char_count: int = 0,
    error: str = "",
) -> str:
    entry_id = uuid.uuid4().hex[:12]
    conn = _connect()
    with _lock:
        conn.execute(
            "INSERT INTO history (id,doc_id,title,mode,engine,model,status,"
            "block_total,block_done,char_count,created_at,finished_at,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                entry_id,
                doc_id,
                title,
                mode,
                engine,
                model,
                status,
                block_total,
                block_done,
                char_count,
                now(),
                now() if status in ("done", "failed", "cancelled") else None,
                error,
            ),
        )
        conn.commit()
    return entry_id


def update_history(entry_id: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "status",
        "block_done",
        "block_total",
        "char_count",
        "engine",
        "error",
        "finished_at",
    }
    sets = []
    params: list[Any] = []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key}=?")
            params.append(value)
    if not sets:
        return
    params.append(entry_id)
    conn = _connect()
    with _lock:
        conn.execute(f"UPDATE history SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()


def list_history(limit: int = 100, query: str = "") -> list[dict[str, Any]]:
    conn = _connect()
    sql = "SELECT * FROM history"
    params: list[Any] = []
    if query:
        sql += " WHERE title LIKE ? OR engine LIKE ? OR status LIKE ?"
        like = f"%{query}%"
        params += [like, like, like]
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    with _lock:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def clear_history() -> None:
    conn = _connect()
    with _lock:
        conn.execute("DELETE FROM history")
        conn.commit()


def export_all() -> dict[str, Any]:
    conn = _connect()
    with _lock:
        docs = [dict(r) for r in conn.execute("SELECT * FROM documents").fetchall()]
        blocks = [dict(r) for r in conn.execute("SELECT * FROM blocks").fetchall()]
        history = [dict(r) for r in conn.execute("SELECT * FROM history").fetchall()]
    for doc in docs:
        doc["meta"] = _load(doc.pop("meta_json"), {})
        doc["toc"] = _load(doc.pop("toc_json"), [])
    for block in blocks:
        block["data"] = _load(block.pop("data_json"), {})
        block["translation"] = _load(block.pop("tr_json"), None)
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "exported_at": now(),
        "documents": docs,
        "blocks": blocks,
        "history": history,
    }


def delete_all_documents() -> None:
    conn = _connect()
    with _lock:
        conn.execute("DELETE FROM blocks")
        conn.execute("DELETE FROM documents")
        conn.commit()


# ------------------------------------------------------------------- settings
def get_settings() -> dict[str, Any]:
    conn = _connect()
    with _lock:
        rows = conn.execute("SELECT key,value FROM settings").fetchall()
    data = dict(config.DEFAULT_SETTINGS)
    for row in rows:
        try:
            data[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            data[row["key"]] = row["value"]
    return data


def set_settings(values: dict[str, Any]) -> dict[str, Any]:
    conn = _connect()
    with _lock:
        for key, value in values.items():
            if key not in config.DEFAULT_SETTINGS:
                continue
            conn.execute(
                "INSERT INTO settings (key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, _dump(value)),
            )
        conn.commit()
    return get_settings()


# ----------------------------------------------------------------- dict cache
def get_dict_cache(word: str, context_hash: str) -> dict[str, Any] | None:
    conn = _connect()
    with _lock:
        row = conn.execute(
            "SELECT data_json FROM dict_cache WHERE word=? AND context_hash=?",
            (word, context_hash),
        ).fetchone()
    return _load(row["data_json"], None) if row else None


def save_dict_cache(word: str, context_hash: str, data: dict[str, Any]) -> None:
    conn = _connect()
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO dict_cache (word,context_hash,data_json,created_at) "
            "VALUES (?,?,?,?)",
            (word, context_hash, _dump(data), now()),
        )
        conn.commit()