-- Vizara operator console — schema v1.
-- Admin data is local-only. Customer chat history stays in the bot's own DB.

CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash   TEXT PRIMARY KEY,
    csrf_token   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    ip           TEXT,
    user_agent   TEXT,
    flash_message TEXT,
    flash_kind    TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    ip     TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS clients (
    id             TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    domain         TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    contact_name   TEXT,
    contact_email  TEXT,
    notes          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'USD',
    interval        TEXT NOT NULL DEFAULT 'month',
    setup_fee_cents INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id          TEXT NOT NULL REFERENCES clients(id),
    plan_id            INTEGER NOT NULL REFERENCES plans(id),
    status             TEXT NOT NULL DEFAULT 'trial',
    started_on         TEXT NOT NULL,
    current_period_end TEXT,
    cancel_at          TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       TEXT NOT NULL REFERENCES clients(id),
    subscription_id INTEGER REFERENCES subscriptions(id),
    number          TEXT NOT NULL UNIQUE,
    issued_on       TEXT NOT NULL,
    due_on          TEXT,
    amount_cents    INTEGER NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'USD',
    status          TEXT NOT NULL DEFAULT 'draft',
    paid_on         TEXT,
    method          TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_links (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id    TEXT NOT NULL REFERENCES clients(id),
    invoice_id   INTEGER REFERENCES invoices(id),
    provider     TEXT NOT NULL DEFAULT 'lemonsqueezy',
    kind         TEXT NOT NULL DEFAULT 'checkout',
    label        TEXT NOT NULL,
    url          TEXT NOT NULL,
    amount_cents INTEGER,
    currency     TEXT NOT NULL DEFAULT 'USD',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider        TEXT NOT NULL DEFAULT 'groq',
    label           TEXT NOT NULL,
    ciphertext      TEXT NOT NULL,
    hint            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    weight          INTEGER NOT NULL DEFAULT 1,
    cooldown_until  TEXT,
    last_checked_at TEXT,
    last_used_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS key_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    key_id            INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    ts                TEXT NOT NULL,
    model             TEXT,
    status            TEXT NOT NULL,
    http_status       INTEGER,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens      INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER,
    error_code        TEXT
);

CREATE TABLE IF NOT EXISTS conversation_notes (
    conversation_id INTEGER PRIMARY KEY,
    client_id       TEXT,
    resolved_at     TEXT NOT NULL,
    resolved_by     TEXT NOT NULL,
    note            TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON admin_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_subscriptions_client ON subscriptions(client_id);
CREATE INDEX IF NOT EXISTS idx_invoices_client ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_payment_links_client ON payment_links(client_id);
CREATE INDEX IF NOT EXISTS idx_key_usage_key_ts ON key_usage(key_id, ts);
