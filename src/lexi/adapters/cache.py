"""Provider response cache (13): SQLite WAL shared by the server and the Twin process.

live          every call goes to the provider (responses are still stored)
read_through  a hit is served from the cache, a miss calls the provider and stores the response
replay        the provider is never called; adapters report an error on a miss
The same file carries the Twin's announcements to the simulated Prisma (sim_utterances).
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

MODES = ("live", "read_through", "replay")


class ProviderCache:
    def __init__(self, directory: Path, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"cache mode must be one of {MODES}, got {mode!r}")
        directory.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self._lock = threading.Lock()
        self._db = sqlite3.connect(directory / "cache.db", check_same_thread=False, timeout=10, isolation_level=None)
        self._db.execute("PRAGMA busy_timeout=10000")
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        self._db.execute("CREATE TABLE IF NOT EXISTS responses (provider TEXT, key TEXT, value BLOB, PRIMARY KEY (provider, key))")
        self._db.execute("CREATE TABLE IF NOT EXISTS sim_utterances (utt_id INTEGER PRIMARY KEY, entry TEXT)")

    @property
    def serves_hits(self) -> bool:
        return self.mode != "live"

    @property
    def stores(self) -> bool:
        return self.mode != "replay"

    def get(self, provider: str, key: str) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM responses WHERE provider=? AND key=?", (provider, key)).fetchone()
        return bytes(row[0]) if row else None

    def put(self, provider: str, key: str, value: bytes) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?)", (provider, key, value))

    def put_sim_utterance(self, utt_id: int, entry: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO sim_utterances VALUES (?,?)", (utt_id, json.dumps(entry, ensure_ascii=False)))

    def get_sim_utterance(self, utt_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT entry FROM sim_utterances WHERE utt_id=?", (utt_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self._db.close()
