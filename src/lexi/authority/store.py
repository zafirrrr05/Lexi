"""certs.db (C6 OWNS): certificates, every published snapshot, and the shift onsets that decide which records recertification may use."""

import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from lexi.common.types import Certificate, RegistrySnapshot

DDL = """
CREATE TABLE IF NOT EXISTS certificates (cert_id TEXT PRIMARY KEY, cell TEXT NOT NULL, rung INTEGER NOT NULL, issued_at REAL NOT NULL,
                                         state TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (version INTEGER PRIMARY KEY, ts REAL NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS onsets (cell TEXT PRIMARY KEY, ts REAL NOT NULL);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(DDL)

    def last_snapshot(self) -> RegistrySnapshot | None:
        row = self.db.execute("SELECT body FROM snapshots ORDER BY version DESC LIMIT 1").fetchone()
        return RegistrySnapshot.model_validate_json(row[0]) if row else None

    def save(self, snap: RegistrySnapshot, certs: Sequence[Certificate] = (), superseded_cells: Iterable[str] = (),
             cleared_onsets: Iterable[str] = ()) -> None:
        """One transaction: a restart sees either the old registry or the new one, never half."""
        with self.db:
            self.db.executemany("DELETE FROM onsets WHERE cell = ?", [(c,) for c in cleared_onsets])
            for cell in superseded_cells:
                self.db.execute("UPDATE certificates SET state = 'SUPERSEDED', body = json_set(body, '$.state', 'SUPERSEDED') "
                                "WHERE cell = ? AND state = 'ACTIVE'", (cell,))
            self.db.executemany("INSERT INTO certificates VALUES (?, ?, ?, ?, ?, ?)",
                                [(c.cert_id, c.cell, c.rung, c.issued_at, c.state, c.model_dump_json()) for c in certs])
            self.db.execute("INSERT INTO snapshots VALUES (?, ?, ?)", (snap.version, snap.ts, snap.model_dump_json()))

    def certificates(self, cell: str | None = None) -> list[Certificate]:
        rows = self.db.execute("SELECT body FROM certificates WHERE ? IS NULL OR cell = ? ORDER BY issued_at, rung", (cell, cell)).fetchall()
        return [Certificate.model_validate_json(r[0]) for r in rows]

    def onsets(self) -> dict[str, float]:
        return dict(self.db.execute("SELECT cell, ts FROM onsets").fetchall())

    def set_onset(self, cell: str, ts: float) -> None:
        with self.db:
            self.db.execute("INSERT INTO onsets VALUES (?, ?) ON CONFLICT(cell) DO UPDATE SET ts = MIN(ts, excluded.ts)", (cell, ts))

    def clear_onset(self, cell: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM onsets WHERE cell = ?", (cell,))

    def close(self) -> None:
        self.db.close()
