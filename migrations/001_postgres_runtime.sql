-- AKSynxa V3 initial PostgreSQL runtime tables.
-- The current V2-compatible PostgresStore also creates these idempotently.
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
CREATE INDEX IF NOT EXISTS idx_aksynxa_processed_at ON aksynxa_processed_messages(processed_at);
