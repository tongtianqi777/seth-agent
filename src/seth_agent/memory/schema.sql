-- seth-agent memory store (SQLite). Applied on every start; every statement is idempotent.

-- ---------------------------------------------------------------------------
-- Agent bookkeeping (shared by all extractors)
-- ---------------------------------------------------------------------------

-- Key/value state, e.g. the ingested_at high-water mark and the last poll time.
CREATE TABLE IF NOT EXISTS agent_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Which source messages each extractor has handled, and the body it saw.
-- A message is handled again when its body changes (e.g. an album caption arrives late).
CREATE TABLE IF NOT EXISTS processed_messages (
    extractor    TEXT NOT NULL,
    chat_jid     TEXT NOT NULL,
    message_id   TEXT NOT NULL,
    body_hash    TEXT NOT NULL,
    outcome      TEXT NOT NULL,           -- skipped | extracted | failed
    processed_at TEXT NOT NULL,
    PRIMARY KEY (extractor, chat_jid, message_id)
);

-- Batches an extractor gave up on (bad request, refusal, unparseable output).
CREATE TABLE IF NOT EXISTS extraction_errors (
    id          INTEGER PRIMARY KEY,
    extractor   TEXT NOT NULL,
    message_ids TEXT NOT NULL,            -- JSON array of [chat_jid, message_id]
    error       TEXT NOT NULL,
    at          TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Calendar memory
-- ---------------------------------------------------------------------------

-- One row per known event. Fuzzy dates are stored as a span:
-- "Nov 2026" is date_start 2026-11-01, date_end 2026-11-30, precision 'month'.
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    title      TEXT NOT NULL,
    details    TEXT,
    date_start TEXT NOT NULL,             -- YYYY-MM-DD, inclusive
    date_end   TEXT NOT NULL,             -- YYYY-MM-DD, inclusive
    precision  TEXT NOT NULL CHECK (precision IN ('day', 'range', 'week', 'month', 'year')),
    time       TEXT,                      -- free-form local time if known, e.g. "15:30" or "evening"
    status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    confidence REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_dates ON events (status, date_start, date_end);

-- The messages that created, changed or cancelled an event.
CREATE TABLE IF NOT EXISTS event_sources (
    event_id   INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    chat_jid   TEXT NOT NULL,
    message_id TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('created', 'updated', 'cancelled')),
    PRIMARY KEY (event_id, chat_jid, message_id, role)
);
CREATE INDEX IF NOT EXISTS idx_event_sources_message ON event_sources (chat_jid, message_id);

-- Audit trail of every change to an event.
CREATE TABLE IF NOT EXISTS event_history (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    op          TEXT NOT NULL,            -- add | update | cancel
    before_json TEXT,
    after_json  TEXT,
    reason      TEXT,
    at          TEXT NOT NULL
);
