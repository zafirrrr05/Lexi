"""Test-split custody (C14 step 6, P5): only C15 may read it, once per release tag, and every access is logged."""

import sqlite3

from lexi.common.errors import CustodyError

READER = "C15"


def forbid_test(split: str) -> None:
    if split == "Test":
        raise CustodyError("the Test split is served only through test_split() to C15")


def serve(db: sqlite3.Connection, caller: str, release_tag: str, now: float, n: int) -> None:
    """Called after the rows were selected; refuses other callers and a repeat of the same tag, logs the access."""
    if caller != READER:
        raise CustodyError(f"{caller} may not read the Test split")
    if db.execute("SELECT 1 FROM custody_log WHERE release_tag=?", (release_tag,)).fetchone():
        raise CustodyError(f"Test split already served for release {release_tag}")
    db.execute("INSERT INTO custody_log (ts, caller, release_tag, n) VALUES (?,?,?,?)", (now, caller, release_tag, n))
