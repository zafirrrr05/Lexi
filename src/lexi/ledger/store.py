"""SQLite WAL access for C9. Plain SQL; every mutation goes through Ledger, which serialises writers."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = Path(__file__).with_name("schema.sql")
# Effect of an entry on what the party owes: payment received lowers it, goods on credit raise it (4.3).
SIGN = {"CREDIT_ENTRY": -1, "DEBIT_ENTRY": 1, "PROMISE_TO_PAY": 0, "RESCHEDULE_DUE": 0}
STATUS = {"PREPARED": "PENDING", "DELIVERED": "PENDING", "POSTED": "POSTED", "COMPENSATED": "UNDONE", "REJECTED": "REJECTED"}


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA.read_text())

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def txn(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    def seed(self, accounts: list[dict[str, Any]]) -> None:
        with self.txn() as db:
            for a in accounts:
                db.execute("INSERT OR IGNORE INTO accounts VALUES (?, ?)", (a["account"], a.get("lang", "hi")))
                for p in a["parties"]:
                    db.execute("INSERT OR IGNORE INTO parties VALUES (?,?,?,?,?,?,?,?)", (
                        a["account"], p["id"], p["display"], json.dumps(p["aliases"], ensure_ascii=False),
                        p["ipk"], p["balance"], json.dumps(p["amount_hist"]), p.get("last_entry_ts")))

    # ---- reads
    def account_of(self, account: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM accounts WHERE account=?", (account,)).fetchone()

    def party(self, account: str, party_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM parties WHERE account=? AND id=?", (account, party_id)).fetchone()

    def parties(self, account: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM parties WHERE account=? ORDER BY id", (account,)).fetchall()

    def session(self, session_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()

    def entry(self, entry_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM entries WHERE entry_id=?", (entry_id,)).fetchone()

    def entry_by_key(self, key: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM entries WHERE idempotency_key=?", (key,)).fetchone()

    def events(self, entry_id: str) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM entry_events WHERE entry_id=? ORDER BY id", (entry_id,)).fetchall()

    def last_event(self, entry_id: str) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM entry_events WHERE entry_id=? ORDER BY id DESC LIMIT 1", (entry_id,)).fetchone()

    def entries_of_session(self, session_id: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT e.*, v.event AS last, v.ts AS last_ts FROM entries e JOIN entry_events v ON v.id="
            "(SELECT MAX(id) FROM entry_events WHERE entry_id=e.entry_id) WHERE e.session_id=? ORDER BY e.created_ts", (session_id,)).fetchall()

    def open_entries(self) -> list[sqlite3.Row]:
        """Entries whose latest event is PREPARED or DELIVERED (crash recovery, 3b)."""
        return self.db.execute(
            "SELECT e.*, v.event AS last, v.window_deadline FROM entries e JOIN entry_events v ON v.id="
            "(SELECT MAX(id) FROM entry_events WHERE entry_id=e.entry_id) WHERE v.event IN ('PREPARED','DELIVERED')").fetchall()

    def unaudited_events(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT v.*, e.session_id, e.intent, e.party_id, e.amount_inr, e.date, e.provenance, e.decision_id, e.audit_seq "
            "FROM entry_events v JOIN entries e USING (entry_id) WHERE v.id NOT IN (SELECT event_id FROM audited_events) ORDER BY v.id"
        ).fetchall()

    def posted_entries(self, account: str, party_id: str | None = None) -> list[sqlite3.Row]:
        q = ("SELECT e.* FROM entries e WHERE e.account=? AND (SELECT event FROM entry_events v WHERE v.entry_id=e.entry_id "
             "ORDER BY id DESC LIMIT 1)='POSTED'")
        args: tuple[Any, ...] = (account,)
        if party_id:
            q, args = q + " AND e.party_id=?", (account, party_id)
        return self.db.execute(q + " ORDER BY created_ts", args).fetchall()

    def pending_entries(self, account: str, party_id: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT e.* FROM entries e WHERE e.account=? AND e.party_id=? AND (SELECT event FROM entry_events v WHERE "
            "v.entry_id=e.entry_id ORDER BY id DESC LIMIT 1) IN ('PREPARED','DELIVERED')", (account, party_id)).fetchall()

    def balance(self, account: str, party_id: str, *, tentative: bool = False) -> int:
        """balance = opening + fold of POSTED entries (+ PENDING ones when tentative)."""
        bal = self.party(account, party_id)["opening"]  # type: ignore[index]
        rows = self.posted_entries(account, party_id) + (self.pending_entries(account, party_id) if tentative else [])
        return int(bal + sum(SIGN[r["intent"]] * (r["amount_inr"] or 0) for r in rows))

    # ---- writes (callers hold the ledger lock and, for multi-statement writes, a txn)
    def insert_entry(self, **f: Any) -> None:
        cols = ", ".join(f)
        self.db.execute(f"INSERT INTO entries ({cols}) VALUES ({', '.join('?' * len(f))})", tuple(f.values()))

    def add_event(self, entry_id: str, event: str, ts: float, deadline: float | None = None, reason: str = "") -> int:
        cur = self.db.execute("INSERT INTO entry_events (entry_id, event, ts, window_deadline, reason) VALUES (?,?,?,?,?)",
                              (entry_id, event, ts, deadline, reason))
        assert cur.lastrowid is not None
        return cur.lastrowid

    def mark_audited(self, event_id: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO audited_events VALUES (?)", (event_id,))

    def open_session(self, session_id: str, account: str, ts: float) -> None:
        self.db.execute("INSERT OR IGNORE INTO sessions (session_id, account, opened_ts) VALUES (?,?,?)", (session_id, account, ts))

    def close_session(self, session_id: str, ts: float) -> None:
        self.db.execute("UPDATE sessions SET closed_ts=? WHERE session_id=? AND closed_ts IS NULL", (ts, session_id))
