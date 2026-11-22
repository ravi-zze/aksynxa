-- AKSynxa V3.1 data layer tables (PostgreSQL).
-- The app also creates these idempotently at startup (app/data_layer.py
-- PG_SCHEMA), so this file is reference/documentation for DBAs.
-- Every table carries brand_slug (the tenant key); every application
-- query filters on it.
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
