"""SQLite storage for conversations, drafts, and the learned style guide."""

import os
import sqlite3
import time

DB_PATH = os.environ.get("DB_PATH", "goldclub.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,              -- 'customer' or 'agent'
    text TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    customer_name TEXT,
    customer_message TEXT NOT NULL,
    category TEXT,
    ai_draft TEXT NOT NULL,
    final_reply TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / edited / rejected
    admin_msg_id INTEGER,
    edit_msg_id INTEGER,
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status, decided_at);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_conn = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


# --- conversation history ---

def add_message(chat_id: int, role: str, text: str) -> None:
    c = conn()
    c.execute(
        "INSERT INTO messages (chat_id, role, text, created_at) VALUES (?, ?, ?, ?)",
        (chat_id, role, text, time.time()),
    )
    c.commit()


def get_history(chat_id: int, limit: int = 8) -> list[sqlite3.Row]:
    rows = conn().execute(
        "SELECT role, text FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    return list(reversed(rows))


# --- drafts / approval queue ---

def create_draft(chat_id: int, customer_name: str, customer_message: str,
                 category: str, ai_draft: str) -> int:
    c = conn()
    cur = c.execute(
        "INSERT INTO drafts (chat_id, customer_name, customer_message, category, "
        "ai_draft, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, customer_name, customer_message, category, ai_draft, time.time()),
    )
    c.commit()
    return cur.lastrowid


def set_admin_msg(draft_id: int, admin_msg_id: int) -> None:
    c = conn()
    c.execute("UPDATE drafts SET admin_msg_id = ? WHERE id = ?", (admin_msg_id, draft_id))
    c.commit()


def set_edit_msg(draft_id: int, edit_msg_id: int) -> None:
    c = conn()
    c.execute("UPDATE drafts SET edit_msg_id = ? WHERE id = ?", (edit_msg_id, draft_id))
    c.commit()


def get_draft(draft_id: int) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def find_draft_by_admin_reply(replied_msg_id: int) -> sqlite3.Row | None:
    """Match a worker's reply message to an open draft (review msg or edit prompt)."""
    return conn().execute(
        "SELECT * FROM drafts WHERE (admin_msg_id = ? OR edit_msg_id = ?) "
        "AND status IN ('pending', 'rejected') ORDER BY id DESC LIMIT 1",
        (replied_msg_id, replied_msg_id),
    ).fetchone()


def decide_draft(draft_id: int, status: str, final_reply: str | None) -> None:
    c = conn()
    c.execute(
        "UPDATE drafts SET status = ?, final_reply = ?, decided_at = ? WHERE id = ?",
        (status, final_reply, time.time(), draft_id),
    )
    c.commit()


# --- learning data ---

def get_examples(limit: int = 200) -> list[sqlite3.Row]:
    """Approved/edited exchanges, newest first — the few-shot pool."""
    return conn().execute(
        "SELECT customer_message, ai_draft, final_reply, status, category "
        "FROM drafts WHERE status IN ('approved', 'edited') "
        "ORDER BY decided_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def get_edit_pairs(limit: int = 20) -> list[sqlite3.Row]:
    return conn().execute(
        "SELECT customer_message, ai_draft, final_reply FROM drafts "
        "WHERE status = 'edited' ORDER BY decided_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def count_decided() -> int:
    return conn().execute(
        "SELECT COUNT(*) FROM drafts WHERE status IN ('approved', 'edited')"
    ).fetchone()[0]


def stats() -> dict:
    rows = conn().execute(
        "SELECT status, COUNT(*) AS n FROM drafts GROUP BY status"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


# --- key/value (style guide, counters) ---

def kv_get(key: str, default: str = "") -> str:
    row = conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def kv_set(key: str, value: str) -> None:
    c = conn()
    c.execute(
        "INSERT INTO kv (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    c.commit()
