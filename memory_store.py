"""
J.A.R.V.I.S — SQLite 대화·사용자 선호 영속 메모리

환경변수:
  JARVIS_MEMORY_DB         DB 경로 (기본 data/jarvis_memory.db)
  JARVIS_MEMORY_ENABLED    1 — SQLite 저장/로드 (기본 ON)
  JARVIS_MEMORY_TURNS      프롬프트에 넣을 최근 턴 수 (기본 6)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def memory_enabled() -> bool:
    return os.getenv("JARVIS_MEMORY_ENABLED", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def memory_db_path() -> Path:
    raw = os.getenv("JARVIS_MEMORY_DB", "data/jarvis_memory.db").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    return p


def memory_turn_limit() -> int:
    try:
        return max(2, int(os.getenv("JARVIS_MEMORY_TURNS", "6")))
    except ValueError:
        return 6


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    path = memory_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(path), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("PRAGMA synchronous=NORMAL")
    _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

        CREATE TABLE IF NOT EXISTS prefs (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.commit()


def init_db() -> None:
    if not memory_enabled():
        return
    with _lock:
        _get_conn()


def load_history(limit: int | None = None) -> list[dict[str, str]]:
    """부팅 시 LLM history 복원."""
    if not memory_enabled():
        return []
    lim = limit or max(4, int(os.getenv("JARVIS_MAX_HISTORY", "40")))
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (lim,),
        ).fetchall()
    out = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    # Gemini/Ollama 규칙: 첫 메시지 user
    while out and out[0].get("role") != "user":
        out.pop(0)
    return out


def save_message(role: str, content: str) -> None:
    if not memory_enabled():
        return
    text = (content or "").strip()
    if not text or role not in ("user", "assistant", "system"):
        return
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, text, datetime.now().timestamp()),
        )
        max_h = max(4, int(os.getenv("JARVIS_MAX_HISTORY", "40")))
        conn.execute(
            """
            DELETE FROM messages WHERE id NOT IN (
                SELECT id FROM messages ORDER BY id DESC LIMIT ?
            )
            """,
            (max_h,),
        )
        conn.commit()


def save_exchange(user_text: str, assistant_text: str) -> None:
    save_message("user", user_text)
    save_message("assistant", assistant_text)


def record_pref(key: str, value: str = "", *, increment: int = 1) -> None:
    """사용자 선호·앱 사용 빈도 기록."""
    if not memory_enabled():
        return
    k = (key or "").strip()
    if not k:
        return
    v = (value or k).strip()
    now = datetime.now().timestamp()
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT count FROM prefs WHERE key = ?", (k,)).fetchone()
        if row:
            conn.execute(
                "UPDATE prefs SET value=?, count=count+?, updated_at=? WHERE key=?",
                (v, max(1, increment), now, k),
            )
        else:
            conn.execute(
                "INSERT INTO prefs (key, value, count, updated_at) VALUES (?, ?, ?, ?)",
                (k, v, max(1, increment), now),
            )
        conn.commit()


def record_app_open(app_name: str) -> None:
    name = (app_name or "").strip()
    if name:
        record_pref(f"app:{name}", name)


def top_prefs(prefix: str = "", limit: int = 5) -> list[dict[str, Any]]:
    if not memory_enabled():
        return []
    with _lock:
        conn = _get_conn()
        if prefix:
            rows = conn.execute(
                "SELECT key, value, count FROM prefs WHERE key LIKE ? "
                "ORDER BY count DESC, updated_at DESC LIMIT ?",
                (f"{prefix}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT key, value, count FROM prefs ORDER BY count DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [{"key": r["key"], "value": r["value"], "count": r["count"]} for r in rows]


def get_memory_context_suffix() -> str:
    """LLM system prompt — 최근 대화 요약 + 자주 쓰는 앱/패턴."""
    if not memory_enabled():
        return ""
    lines: list[str] = []

    apps = top_prefs("app:", limit=5)
    if apps:
        lines.append("\n\n[USER HABITS — frequently opened apps]")
        for a in apps:
            lines.append(f"  · {a['value']} ({a['count']}회)")

    other = [p for p in top_prefs(limit=8) if not p["key"].startswith("app:")]
    if other:
        lines.append("\n[USER PREFS]")
        for p in other[:5]:
            lines.append(f"  · {p['key']}: {p['value']}")

    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (memory_turn_limit() * 2,),
        ).fetchall()
    if rows:
        lines.append("\n[RECENT MEMORY — persisted across sessions]")
        for r in reversed(rows):
            role = "User" if r["role"] == "user" else "JARVIS"
            text = (r["content"] or "")[:180].replace("\n", " ")
            if text:
                lines.append(f"  {role}: {text}")

    if not lines:
        return ""
    lines.append(
        '\nUse this memory for deictic references like "아까 그거", "그 종목", "그 문서".'
    )
    return "\n".join(lines)


def stats() -> dict[str, Any]:
    if not memory_enabled():
        return {"enabled": False}
    with _lock:
        conn = _get_conn()
        msg_n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        pref_n = conn.execute("SELECT COUNT(*) FROM prefs").fetchone()[0]
    return {
        "enabled": True,
        "db": str(memory_db_path()),
        "messages": msg_n,
        "prefs": pref_n,
    }


def close_db() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
