-- AKSynxa V3.2 — integrations tables (reference only)
-- App startup par app/data_layer.py yahi tables idempotently bana
-- deta hai (SQLite + PostgreSQL dono). Ye file sirf documentation /
-- manual inspection ke liye hai. PostgreSQL dialect me api_keys.id
-- "BIGINT GENERATED ALWAYS AS IDENTITY" banta hai; baaki same hai.

CREATE TABLE IF NOT EXISTS api_keys (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_slug  TEXT NOT NULL,
    key_hash    TEXT NOT NULL UNIQUE,   -- SHA-256 hex; plaintext kabhi nahi
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_api_keys_brand ON api_keys(brand_slug);

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id          TEXT PRIMARY KEY,       -- uuid hex
    brand_slug  TEXT NOT NULL,
    url         TEXT NOT NULL,
    events      TEXT NOT NULL DEFAULT '[]',  -- JSON array of event names
    secret      TEXT NOT NULL DEFAULT '',    -- HMAC-SHA256 signing secret
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hooks_brand ON webhook_subscriptions(brand_slug, active);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              TEXT PRIMARY KEY,   -- uuid hex
    subscription_id TEXT NOT NULL,
    brand_slug      TEXT NOT NULL,
    event           TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_retry_at   TEXT,
    last_error      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON webhook_deliveries(status, next_retry_at);
