"""AKSynxa V3.1 — unified data layer (leads / conversations / reminders /
organizations / business users).

V2 me ye data alag-alag JSON/CSV files me bikhra tha (leads_<brand>.json,
chatlog_<brand>.json, reminders.json). V3.1 me DATABASE hi source of
truth hai:

  * DATABASE_URL set ho  -> PostgreSQL (psycopg v3, lazy import —
    module bina psycopg ke bhi import ho jata hai).
  * DATABASE_URL na ho   -> SQLite (stdlib), file <data_dir>/aksynxa.db.
    Zero external service — app aur tests aise hi chalte hain.

HAR table me `brand_slug` (tenant key) hai aur HAR query usi se filter
hoti hai — ek brand ka data dusre brand ko kabhi nahi dikhega. Sessions
aur webhook dedupe ab bhi app/store.py / app/postgres_store.py me hain
(unki apni tables, wahi brand_slug pattern).

File-based JSON/CSV writes (leads.py / sheets.py) ek export mirror ke
roop me chalte rehte hain, lekin READS hamesha DB se aate hain.
"""
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

from app.store import default_data_dir

log = logging.getLogger("aksynxa.data_layer")

# ---------------------------------------------------------------------------
# Schemas — SQLite aur PostgreSQL, ek hi shape
# ---------------------------------------------------------------------------
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    brand_slug  TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_slug      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    brand_name      TEXT NOT NULL DEFAULT '',
    customer_number TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    interest        TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_brand ON leads(brand_slug, id);
CREATE TABLE IF NOT EXISTS chat_turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_slug      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    brand_name      TEXT NOT NULL DEFAULT '',
    customer_number TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    direction       TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chat_brand ON chat_turns(brand_slug, id);
CREATE TABLE IF NOT EXISTS reminders (
    id           TEXT PRIMARY KEY,
    brand_slug   TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT '',
    user_id      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    due_at       TEXT,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_brand ON reminders(brand_slug, status);
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_slug    TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_brand ON users(brand_slug);
CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_slug  TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_keys_brand ON api_keys(brand_slug);
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id          TEXT PRIMARY KEY,
    brand_slug  TEXT NOT NULL,
    url         TEXT NOT NULL,
    events      TEXT NOT NULL DEFAULT '[]',
    secret      TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hooks_brand ON webhook_subscriptions(brand_slug, active);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    brand_slug      TEXT NOT NULL,
    event           TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON webhook_deliveries(status, next_retry_at);
"""

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS organizations (
    brand_slug  TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS leads (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    brand_slug      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    brand_name      TEXT NOT NULL DEFAULT '',
    customer_number TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    interest        TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_leads_brand ON leads(brand_slug, id);
CREATE TABLE IF NOT EXISTS chat_turns (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    brand_slug      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    brand_name      TEXT NOT NULL DEFAULT '',
    customer_number TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    direction       TEXT NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chat_brand ON chat_turns(brand_slug, id);
CREATE TABLE IF NOT EXISTS reminders (
    id           TEXT PRIMARY KEY,
    brand_slug   TEXT NOT NULL,
    type         TEXT NOT NULL DEFAULT '',
    user_id      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    due_at       TEXT,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_brand ON reminders(brand_slug, status);
CREATE TABLE IF NOT EXISTS users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    brand_slug    TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'staff',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_brand ON users(brand_slug);
CREATE TABLE IF NOT EXISTS api_keys (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    brand_slug  TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_keys_brand ON api_keys(brand_slug);
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id          TEXT PRIMARY KEY,
    brand_slug  TEXT NOT NULL,
    url         TEXT NOT NULL,
    events      TEXT NOT NULL DEFAULT '[]',
    secret      TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hooks_brand ON webhook_subscriptions(brand_slug, active);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    brand_slug      TEXT NOT NULL,
    event           TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON webhook_deliveries(status, next_retry_at);
"""

LEAD_KEYS = ("timestamp", "brand", "customer_number", "name", "interest",
             "stage", "summary")
CHAT_KEYS = ("timestamp", "brand", "customer_number", "name", "direction",
             "message", "stage")
USER_KEYS = ("id", "brand_slug", "email", "password_hash", "role", "created_at")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DataLayer:
    """SQLite implementation (default). PostgreSQL ke liye
    PostgresDataLayer — same interface, sirf dialect alag."""

    dialect = "sqlite"

    def __init__(self, db_path: str | None = None, data_dir: str | None = None):
        if db_path is None:
            data_dir = data_dir or default_data_dir()
            db_path = os.path.join(data_dir, "aksynxa.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SQLITE_SCHEMA)
        self._conn.commit()
        log.info("Data layer ready (SQLite): %s", db_path)

    # -- low-level helpers -------------------------------------------------
    def _sql(self, sql: str) -> str:
        return sql  # SQLite: '?' placeholders as-is

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """INSERT/UPDATE/DELETE chalao; nayi row ka id return karo (ya 0)."""
        with self._lock:
            cur = self._conn.execute(self._sql(sql), params)
            self._conn.commit()
            return cur.lastrowid or 0

    def _query(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            rows = self._conn.execute(self._sql(sql), params).fetchall()
        return [dict(r) for r in rows]

    def _query_one(self, sql: str, params: tuple = ()):
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- organizations ------------------------------------------------------
    def ensure_organization(self, brand_slug: str, name: str = "") -> dict:
        self._execute(
            "INSERT INTO organizations (brand_slug, name, created_at) "
            "VALUES (?, ?, ?) ON CONFLICT(brand_slug) DO UPDATE SET "
            "name=excluded.name WHERE excluded.name <> ''",
            (brand_slug, name or "", _utc_now_iso()),
        )
        return self.get_organization(brand_slug)

    def get_organization(self, brand_slug: str):
        return self._query_one(
            "SELECT brand_slug, name, created_at FROM organizations "
            "WHERE brand_slug=?", (brand_slug,))

    def list_organizations(self) -> list:
        return self._query(
            "SELECT brand_slug, name, created_at FROM organizations "
            "ORDER BY brand_slug")

    # -- leads ---------------------------------------------------------------
    # Optional callback (main.py set karta hai): har naye lead par
    # lead_hook(brand_slug, record) — outbound webhooks isi se fire
    # hote hain. Default None = koi side effect nahi (tests/purana
    # behaviour bilkul waisa hi).
    lead_hook = None

    def add_lead(self, brand_slug: str, record: dict) -> None:
        self._execute(
            "INSERT INTO leads (brand_slug, timestamp, brand_name, "
            "customer_number, name, interest, stage, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (brand_slug, record.get("timestamp", ""), record.get("brand", ""),
             record.get("customer_number", ""), record.get("name", ""),
             record.get("interest", ""), record.get("stage", ""),
             record.get("summary", "")),
        )
        hook = self.lead_hook
        if hook is not None:
            try:
                hook(brand_slug, record)
            except Exception as exc:  # hook kabhi lead save ko na roke
                log.warning("lead_hook failed (%s): %s", brand_slug, exc)

    def list_leads(self, brand_slug: str, stage: str | None = None,
                   customer_number: str | None = None) -> list:
        sql = ("SELECT timestamp, brand_name AS brand, customer_number, name, "
               "interest, stage, summary FROM leads WHERE brand_slug=?")
        params: list = [brand_slug]
        if stage is not None:
            sql += " AND stage=?"
            params.append(stage)
        if customer_number is not None:
            sql += " AND customer_number=?"
            params.append(customer_number)
        sql += " ORDER BY id ASC"
        return self._query(sql, tuple(params))

    # -- conversations (chat turns) ------------------------------------------
    def add_chat_turn(self, brand_slug: str, row: dict) -> None:
        self._execute(
            "INSERT INTO chat_turns (brand_slug, timestamp, brand_name, "
            "customer_number, name, direction, message, stage) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (brand_slug, row.get("timestamp", ""), row.get("brand", ""),
             row.get("customer_number", ""), row.get("name", ""),
             row.get("direction", ""), row.get("message", ""),
             row.get("stage", "")),
        )

    def list_chat_turns(self, brand_slug: str) -> list:
        return self._query(
            "SELECT timestamp, brand_name AS brand, customer_number, name, "
            "direction, message, stage FROM chat_turns "
            "WHERE brand_slug=? ORDER BY id ASC", (brand_slug,))

    # -- reminders -------------------------------------------------------------
    def add_reminder(self, item: dict) -> None:
        self._execute(
            "INSERT INTO reminders (id, brand_slug, type, user_id, status, "
            "due_at, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, "
            "due_at=excluded.due_at, payload_json=excluded.payload_json",
            (item["id"], item["brand_slug"], item.get("type", ""),
             item.get("user_id", ""), item.get("status", "pending"),
             item.get("due_at"),
             json.dumps(item, ensure_ascii=False),
             item.get("created_at", "")),
        )

    def list_reminders(self, brand_slug: str | None = None,
                       status: str | None = None) -> list:
        sql = "SELECT payload_json FROM reminders WHERE 1=1"
        params: list = []
        if brand_slug is not None:
            sql += " AND brand_slug=?"
            params.append(brand_slug)
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at ASC, id ASC"
        items = []
        for row in self._query(sql, tuple(params)):
            try:
                items.append(json.loads(row["payload_json"]))
            except (json.JSONDecodeError, TypeError):
                continue
        return items

    def update_reminder_status(self, reminder_id: str, status: str,
                               sent_at: str | None = None,
                               note: str | None = None) -> None:
        row = self._query_one(
            "SELECT payload_json FROM reminders WHERE id=?", (reminder_id,))
        if row is None:
            return
        try:
            item = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            item = {}
        item["status"] = status
        if sent_at is not None:
            item["sent_at"] = sent_at
        if note is not None:
            item["note"] = note
        self._execute(
            "UPDATE reminders SET status=?, payload_json=? WHERE id=?",
            (status, json.dumps(item, ensure_ascii=False), reminder_id),
        )

    # -- API keys (V3.2 — public REST API / embed token) ---------------------
    # Plaintext key KABHI store nahi hoti — sirf SHA-256 hash. Key
    # brand-scoped hai: jo brand key ka hai, API usi ka data degi.
    def create_api_key_record(self, brand_slug: str, key_hash: str,
                              label: str = "") -> dict:
        kid = self._execute(
            "INSERT INTO api_keys (brand_slug, key_hash, label, created_at, "
            "revoked) VALUES (?, ?, ?, ?, 0)",
            (brand_slug, key_hash, label or "", _utc_now_iso()),
        )
        return self.get_api_key(kid)

    def get_api_key(self, key_id):
        if key_id is None:
            return None
        return self._query_one(
            "SELECT id, brand_slug, key_hash, label, created_at, revoked "
            "FROM api_keys WHERE id=?", (key_id,))

    def get_api_key_by_hash(self, key_hash: str):
        return self._query_one(
            "SELECT id, brand_slug, key_hash, label, created_at, revoked "
            "FROM api_keys WHERE key_hash=?", (key_hash,))

    def list_api_keys(self, brand_slug: str) -> list:
        return self._query(
            "SELECT id, brand_slug, key_hash, label, created_at, revoked "
            "FROM api_keys WHERE brand_slug=? ORDER BY id ASC", (brand_slug,))

    def revoke_api_key(self, key_id) -> None:
        self._execute("UPDATE api_keys SET revoked=1 WHERE id=?", (key_id,))

    # -- Outbound webhook subscriptions + delivery queue (V3.2) ---------------
    def create_webhook_subscription(self, sub: dict) -> dict:
        self._execute(
            "INSERT INTO webhook_subscriptions (id, brand_slug, url, events, "
            "secret, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sub["id"], sub["brand_slug"], sub["url"],
             json.dumps(sub.get("events", []), ensure_ascii=False),
             sub.get("secret", ""), 1 if sub.get("active", True) else 0,
             sub.get("created_at", "") or _utc_now_iso()),
        )
        return self.get_webhook_subscription(sub["id"])

    def get_webhook_subscription(self, sub_id: str):
        row = self._query_one(
            "SELECT id, brand_slug, url, events, secret, active, created_at "
            "FROM webhook_subscriptions WHERE id=?", (sub_id,))
        return self._sub_row(row)

    def list_webhook_subscriptions(self, brand_slug: str | None = None,
                                   active_only: bool = False) -> list:
        sql = ("SELECT id, brand_slug, url, events, secret, active, "
               "created_at FROM webhook_subscriptions WHERE 1=1")
        params: list = []
        if brand_slug is not None:
            sql += " AND brand_slug=?"
            params.append(brand_slug)
        if active_only:
            sql += " AND active=1"
        sql += " ORDER BY created_at ASC, id ASC"
        return [self._sub_row(r) for r in self._query(sql, tuple(params))]

    @staticmethod
    def _sub_row(row):
        if row is None:
            return None
        row = dict(row)
        try:
            row["events"] = json.loads(row.get("events") or "[]")
        except (json.JSONDecodeError, TypeError):
            row["events"] = []
        row["active"] = bool(row.get("active"))
        return row

    def add_webhook_delivery(self, item: dict) -> None:
        now = _utc_now_iso()
        self._execute(
            "INSERT INTO webhook_deliveries (id, subscription_id, "
            "brand_slug, event, payload_json, status, attempts, "
            "next_retry_at, last_error, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item["id"], item["subscription_id"], item["brand_slug"],
             item.get("event", ""),
             json.dumps(item.get("payload", {}), ensure_ascii=False),
             item.get("status", "pending"), int(item.get("attempts", 0)),
             item.get("next_retry_at"), item.get("last_error", ""),
             item.get("created_at", "") or now, now),
        )

    def list_webhook_deliveries(self, brand_slug: str | None = None,
                                status: str | None = None) -> list:
        sql = ("SELECT id, subscription_id, brand_slug, event, "
               "payload_json, status, attempts, next_retry_at, last_error, "
               "created_at, updated_at FROM webhook_deliveries WHERE 1=1")
        params: list = []
        if brand_slug is not None:
            sql += " AND brand_slug=?"
            params.append(brand_slug)
        if status is not None:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at ASC, id ASC"
        return [self._delivery_row(r) for r in self._query(sql, tuple(params))]

    def list_due_webhook_deliveries(self, now_iso: str, limit: int = 20) -> list:
        rows = self._query(
            "SELECT id, subscription_id, brand_slug, event, payload_json, "
            "status, attempts, next_retry_at, last_error, created_at, "
            "updated_at FROM webhook_deliveries WHERE status='pending' "
            "AND (next_retry_at IS NULL OR next_retry_at<=?) "
            "ORDER BY created_at ASC, id ASC LIMIT ?",
            (now_iso, int(limit)))
        return [self._delivery_row(r) for r in rows]

    @staticmethod
    def _delivery_row(row):
        row = dict(row)
        try:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            row["payload"] = {}
        return row

    def update_webhook_delivery(self, delivery_id: str, status: str | None = None,
                                attempts: int | None = None,
                                next_retry_at=..., last_error: str | None = None
                                ) -> None:
        sets, params = [], []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if attempts is not None:
            sets.append("attempts=?")
            params.append(int(attempts))
        if next_retry_at is not ...:
            sets.append("next_retry_at=?")
            params.append(next_retry_at)
        if last_error is not None:
            sets.append("last_error=?")
            params.append(last_error)
        if not sets:
            return
        sets.append("updated_at=?")
        params.append(_utc_now_iso())
        params.append(delivery_id)
        self._execute(
            f"UPDATE webhook_deliveries SET {', '.join(sets)} WHERE id=?",
            tuple(params))

    # -- business users ---------------------------------------------------------
    def create_user(self, brand_slug: str, email: str, password_hash: str,
                    role: str = "staff") -> dict:
        email = (email or "").strip().lower()
        if role not in ("owner", "staff"):
            raise ValueError("role 'owner' ya 'staff' hona chahiye")
        if self.get_user_by_email(email) is not None:
            raise ValueError(f"email pehle se registered hai: {email}")
        uid = self._execute(
            "INSERT INTO users (brand_slug, email, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (brand_slug, email, password_hash, role, _utc_now_iso()),
        )
        return self.get_user(uid)

    def get_user(self, user_id):
        if user_id is None:
            return None
        return self._query_one(
            "SELECT id, brand_slug, email, password_hash, role, created_at "
            "FROM users WHERE id=?", (user_id,))

    def get_user_by_email(self, email: str):
        return self._query_one(
            "SELECT id, brand_slug, email, password_hash, role, created_at "
            "FROM users WHERE email=?", ((email or "").strip().lower(),))

    def list_users(self, brand_slug: str) -> list:
        return self._query(
            "SELECT id, brand_slug, email, password_hash, role, created_at "
            "FROM users WHERE brand_slug=? ORDER BY id ASC", (brand_slug,))

    def set_user_password(self, user_id, password_hash: str) -> None:
        self._execute("UPDATE users SET password_hash=? WHERE id=?",
                      (password_hash, user_id))

    def count_users(self, brand_slug: str | None = None) -> int:
        if brand_slug is None:
            row = self._query_one("SELECT COUNT(*) AS n FROM users")
        else:
            row = self._query_one(
                "SELECT COUNT(*) AS n FROM users WHERE brand_slug=?",
                (brand_slug,))
        return int(row["n"]) if row else 0


class PostgresDataLayer(DataLayer):
    """PostgreSQL implementation — psycopg v3 (lazy import, bilkul
    postgres_store.py ke pattern par). Interface DataLayer wali hi hai;
    sirf connection, placeholder (%s) aur schema dialect alag hai."""

    dialect = "postgres"

    def __init__(self, database_url: str):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL selected but psycopg[binary,pool] is not installed"
            ) from exc
        self.database_url = database_url
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=int(os.getenv("DB_POOL_MIN", "1")),
            max_size=int(os.getenv("DB_POOL_MAX", "5")),
            kwargs={"row_factory": dict_row},
            open=True,
        )
        with self.pool.connection() as conn:
            conn.execute(PG_SCHEMA)
        log.info("Data layer ready (PostgreSQL)")

    def _sql(self, sql: str) -> str:
        # SQLite '?' placeholders -> psycopg '%s'; ON CONFLICT syntax dono
        # me same hai, sirf lastrowid ki jagah RETURNING chahiye hota hai
        # (create_user ke liye _execute_pg_insert alag hai).
        return sql.replace("?", "%s")

    def _execute(self, sql: str, params: tuple = ()) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute(self._sql(sql), params)
            return cur.rowcount or 0

    def _execute_insert_id(self, sql: str, params: tuple = ()) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(self._sql(sql) + " RETURNING id", params).fetchone()
        return int(row["id"]) if row else 0

    def _query(self, sql: str, params: tuple = ()) -> list:
        with self.pool.connection() as conn:
            return conn.execute(self._sql(sql), params).fetchall()

    def create_user(self, brand_slug: str, email: str, password_hash: str,
                    role: str = "staff") -> dict:
        email = (email or "").strip().lower()
        if role not in ("owner", "staff"):
            raise ValueError("role 'owner' ya 'staff' hona chahiye")
        if self.get_user_by_email(email) is not None:
            raise ValueError(f"email pehle se registered hai: {email}")
        uid = self._execute_insert_id(
            "INSERT INTO users (brand_slug, email, password_hash, role) "
            "VALUES (?, ?, ?, ?)",
            (brand_slug, email, password_hash, role),
        )
        return self.get_user(uid)

    def create_api_key_record(self, brand_slug: str, key_hash: str,
                              label: str = "") -> dict:
        kid = self._execute_insert_id(
            "INSERT INTO api_keys (brand_slug, key_hash, label, created_at, "
            "revoked) VALUES (?, ?, ?, ?, 0)",
            (brand_slug, key_hash, label or "", _utc_now_iso()),
        )
        return self.get_api_key(kid)

    def close(self) -> None:
        self.pool.close()


def create_data_layer(database_url: str = "", data_dir: str | None = None) -> DataLayer:
    """Factory: DATABASE_URL ho to PostgreSQL, warna SQLite (local)."""
    if database_url:
        return PostgresDataLayer(database_url)
    return DataLayer(data_dir=data_dir)
