"""PostgreSQL implementation of the V2 Store interface.

Keeps the existing Engine API intact while moving sessions and webhook
idempotency into PostgreSQL. Requires psycopg v3.
"""
import json
import logging
import os
from datetime import datetime, timezone


log = logging.getLogger("aksynxa.postgres_store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS aksynxa_sessions (
    brand_slug TEXT NOT NULL,
    user_id TEXT NOT NULL,
    state_json JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (brand_slug, user_id)
);
CREATE TABLE IF NOT EXISTS aksynxa_processed_messages (
    message_id TEXT PRIMARY KEY,
    brand_slug TEXT NOT NULL DEFAULT '',
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_aksynxa_processed_at
    ON aksynxa_processed_messages(processed_at);
"""

class PostgresStore:
    def __init__(self, database_url: str | None = None):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError("PostgreSQL selected but psycopg[binary,pool] is not installed") from exc
        self.database_url = database_url or os.environ["DATABASE_URL"]
        self.pool = ConnectionPool(
            conninfo=self.database_url,
            min_size=int(os.getenv("DB_POOL_MIN", "1")),
            max_size=int(os.getenv("DB_POOL_MAX", "5")),
            kwargs={"row_factory": dict_row},
            open=True,
        )
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)
        log.info("PostgreSQL store ready")

    def get_session(self, brand_slug: str, user_id: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT state_json FROM aksynxa_sessions WHERE brand_slug=%s AND user_id=%s",
                (brand_slug, user_id),
            ).fetchone()
        if not row:
            return None
        value = row["state_json"]
        return value if isinstance(value, dict) else json.loads(value)

    def save_session(self, brand_slug: str, user_id: str, state: dict) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """INSERT INTO aksynxa_sessions (brand_slug,user_id,state_json,updated_at)
                   VALUES (%s,%s,%s::jsonb,%s)
                   ON CONFLICT (brand_slug,user_id) DO UPDATE SET
                   state_json=EXCLUDED.state_json, updated_at=EXCLUDED.updated_at""",
                (brand_slug, user_id, json.dumps(state, ensure_ascii=False), datetime.now(timezone.utc)),
            )

    def delete_session(self, brand_slug: str, user_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM aksynxa_sessions WHERE brand_slug=%s AND user_id=%s", (brand_slug, user_id))

    def is_processed(self, message_id: str) -> bool:
        if not message_id:
            return False
        with self.pool.connection() as conn:
            row = conn.execute("SELECT 1 FROM aksynxa_processed_messages WHERE message_id=%s", (message_id,)).fetchone()
        return row is not None

    def mark_processed(self, message_id: str, brand_slug: str = "") -> None:
        if not message_id:
            return
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO aksynxa_processed_messages(message_id,brand_slug) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (message_id, brand_slug),
            )

    def close(self) -> None:
        self.pool.close()
