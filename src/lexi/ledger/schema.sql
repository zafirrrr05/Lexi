-- C9 ledger.db. Entries and their events are append-only: the triggers make "entries are immutable" a database fact.
CREATE TABLE IF NOT EXISTS accounts (
    account TEXT PRIMARY KEY,
    lang    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parties (
    account       TEXT NOT NULL REFERENCES accounts(account),
    id            TEXT NOT NULL,
    display       TEXT NOT NULL,
    aliases       TEXT NOT NULL,
    ipk           TEXT NOT NULL,
    opening       INTEGER NOT NULL,
    amount_hist   TEXT NOT NULL,
    last_entry_ts REAL,
    PRIMARY KEY (account, id)
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    account    TEXT NOT NULL REFERENCES accounts(account),
    opened_ts  REAL NOT NULL,
    closed_ts  REAL
);
CREATE TABLE IF NOT EXISTS entries (
    entry_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    account         TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    intent          TEXT NOT NULL,
    party_id        TEXT NOT NULL,
    amount_inr      INTEGER,
    date            TEXT,
    provenance      TEXT NOT NULL CHECK (provenance IN ('autonomous', 'human')),
    decision_id     TEXT,
    audit_seq       INTEGER NOT NULL,
    audit_hash      TEXT NOT NULL,
    created_ts      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS entry_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id        TEXT NOT NULL REFERENCES entries(entry_id),
    event           TEXT NOT NULL CHECK (event IN ('PREPARED', 'DELIVERED', 'POSTED', 'COMPENSATED', 'REJECTED')),
    ts              REAL NOT NULL,
    window_deadline REAL,
    reason          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS entry_events_by_entry ON entry_events(entry_id, id);
-- which events already have their LEDGER_EVENT in the audit log (crash recovery appends the rest)
CREATE TABLE IF NOT EXISTS audited_events (event_id INTEGER PRIMARY KEY REFERENCES entry_events(id));

CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON entries BEGIN SELECT RAISE(ABORT, 'entries are immutable'); END;
CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON entries BEGIN SELECT RAISE(ABORT, 'entries are immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON entry_events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON entry_events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
