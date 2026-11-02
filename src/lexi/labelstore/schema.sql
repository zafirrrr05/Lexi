-- C14 labels.db: the only home of gold labels, joined outcomes, calibration records and split assignments.
CREATE TABLE IF NOT EXISTS gold (call_id TEXT PRIMARY KEY, script_id TEXT NOT NULL, split TEXT NOT NULL, json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (call_id TEXT PRIMARY KEY, closed_ts REAL);
CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY, call_id TEXT NOT NULL, stage_index INTEGER NOT NULL, ts REAL NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS decisions_by_call ON decisions (call_id, stage_index, seq);
CREATE TABLE IF NOT EXISTS ledger_events (
    seq INTEGER PRIMARY KEY, call_id TEXT NOT NULL, entry_id TEXT NOT NULL, event TEXT NOT NULL, reason TEXT NOT NULL,
    provenance TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS verdicts (ticket_id TEXT PRIMARY KEY, call_id TEXT NOT NULL, json TEXT NOT NULL, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS calibration (call_id TEXT PRIMARY KEY, cell TEXT NOT NULL, split TEXT NOT NULL, ts REAL NOT NULL, json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS calibration_by_cell ON calibration (split, cell, ts);
-- tier G / A outcomes feed certificates and M2; tier R (undo-derived) lives in ledger_events and is advisory only
CREATE TABLE IF NOT EXISTS outcomes (call_id TEXT NOT NULL, tier TEXT NOT NULL, ts REAL NOT NULL, json TEXT NOT NULL, PRIMARY KEY (call_id, tier));
CREATE TABLE IF NOT EXISTS finalized (call_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS custody_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, caller TEXT NOT NULL, release_tag TEXT NOT NULL, n INTEGER NOT NULL);
