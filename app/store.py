"""Persistent store (SQLite, stdlib only) — sessions + webhook dedupe.

Pehle chat sessions sirf memory me rehte the: server restart = bot sab
bhool jata tha. Ab sessions `data/sessions.db` me rehte hain, taaki
restart ke baad bhi conversation wahi se continue ho jahan chhodi thi.

Isi DB me Meta webhook ke processed message IDs bhi rehte hain — Meta
kabhi-kabhi ek hi message dobara bhejta hai (retries), dedupe se
customer ko duplicate reply nahi jata.
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

log = logging.getLogger("aksynxa.store")


def default_data_dir() -> str:
    """Data folder: AKSYNXA_DATA_DIR env, warna <project>/data."""
    env = os.environ.get("AKSYNXA_DATA_DIR", "").strip()
    if env:
        return env
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "data")


class Store:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.path.join(default_data_dir(), "sessions.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                brand_slug TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (brand_slug, user_id)
            );
            CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT PRIMARY KEY,
                brand_slug TEXT NOT NULL DEFAULT '',
                processed_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()
        log.info("Session store ready: %s", db_path)

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def get_session(self, brand_slug: str, user_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM sessions WHERE brand_slug=? AND user_id=?",
                (brand_slug, user_id),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def save_session(self, brand_slug: str, user_id: str, state: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions (brand_slug, user_id, state_json, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(brand_slug, user_id) "
                "DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
                (brand_slug, user_id, json.dumps(state, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def delete_session(self, brand_slug: str, user_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE brand_slug=? AND user_id=?",
                (brand_slug, user_id),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Webhook dedupe — ek message id sirf ek baar process hoga
    # ------------------------------------------------------------------
    def is_processed(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM processed_messages WHERE message_id=?", (message_id,)
            ).fetchone()
        return row is not None

    def mark_processed(self, message_id: str, brand_slug: str = "") -> None:
        if not message_id:
            return
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, brand_slug, processed_at) "
                "VALUES (?, ?, ?)",
                (message_id, brand_slug, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            self._conn.commit()
