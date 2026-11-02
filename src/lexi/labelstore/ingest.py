"""C14 Label & Calibration Store: ingest gold, verdicts and audit records; join by call_id; guard the Test split."""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lexi.common.catalog import Catalog
from lexi.common.clock import Clock
from lexi.common.types import (
    AuditRecord,
    CalibrationRecord,
    CellStats,
    GoldLabel,
    HumanVerdict,
    LabeledOutcome,
    TestSplit,
    TrainingData,
)
from lexi.labelstore import custody, join, records, splits, stats

SCHEMA = Path(__file__).with_name("schema.sql")


class LabelStore:
    def __init__(self, path: Path, catalog: Catalog, clock: Clock) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.cat, self.clock = catalog, clock
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA.read_text())
        self._outcome_cbs: list[Callable[[LabeledOutcome], None]] = []
        self._call_cbs: list[Callable[[dict[str, Any]], None]] = []

    # ---- ingest (K01, K05, K06)
    def ingest_audit(self, rec: AuditRecord) -> None:
        p, db = rec.payload, self.db
        if rec.type == "DECISION":
            db.execute("INSERT OR IGNORE INTO decisions VALUES (?,?,?,?,?)", (rec.seq, p["call_id"], p["stage_index"], rec.ts, json.dumps(p)))
            return
        if rec.type == "SESSION_EVENT" and p["event"] == "CLOSE":
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?)", (p["session_id"], rec.ts))
            self._finalize(p["session_id"])
        elif rec.type == "LEDGER_EVENT":
            db.execute("INSERT OR IGNORE INTO ledger_events VALUES (?,?,?,?,?,?,?)",
                       (rec.seq, p["session_id"], p["entry_id"], p["event"], p["reason"], p["provenance"], rec.ts))

    def ingest_gold(self, gold: GoldLabel) -> None:
        splits.check(gold)
        self.db.execute("INSERT OR REPLACE INTO gold VALUES (?,?,?,?)", (gold.call_id, gold.script_id, gold.split, gold.model_dump_json()))
        self._finalize(gold.call_id)

    def ingest_verdict(self, v: HumanVerdict) -> None:
        gold = self._gold(v.call_id)
        if gold is None:
            return
        self.db.execute("INSERT OR REPLACE INTO verdicts VALUES (?,?,?,?)", (v.ticket_id, v.call_id, v.model_dump_json(), self.clock.now()))
        row = self.db.execute("SELECT payload FROM decisions WHERE call_id=? ORDER BY seq LIMIT 1", (v.call_id,)).fetchone()
        out = join.human(gold, v.call_id, v.values, json.loads(row[0])["cell"] if row else None, self.clock.now())
        self._store_outcome(out)

    def on_outcome(self, cb: Callable[[LabeledOutcome], None]) -> None:
        self._outcome_cbs.append(cb)

    def on_call(self, cb: Callable[[dict[str, Any]], None]) -> None:
        """Display stream (K16 family): a summary of every finished LIVE call, with the naive baseline's shadow outcome next to LEXI's."""
        self._call_cbs.append(cb)

    # ---- reads
    def calibration_records(self, split: str = "Calib", cell: str | None = None, since_ts: float | None = None) -> list[CalibrationRecord]:
        custody.forbid_test(split)
        q, args = "SELECT json FROM calibration WHERE split=?", [split]
        if cell:
            q, args = q + " AND cell=?", [*args, cell]
        if since_ts is not None:
            q, args = q + " AND ts>=?", [*args, since_ts]
        return [CalibrationRecord.model_validate_json(r[0]) for r in self.db.execute(q + " ORDER BY ts, call_id", args)]

    def first_scores(self, split: str, cell: str, since_ts: float | None = None) -> list[float]:
        """s_0 of every record of a cell, without decoding the records (a polled statistic: C6 and the console ask every second)."""
        custody.forbid_test(split)
        return [r[0] for r in self.db.execute("SELECT json_extract(json, '$.scores[0]') FROM calibration WHERE split=? AND cell=? AND ts>=?",
                                              (split, cell, since_ts if since_ts is not None else float("-inf")))]

    def training_data(self, kind: str, split: str) -> TrainingData:
        custody.forbid_test(split)
        if split == "Calib":
            raise ValueError("Calib supplies p-values to C6 only")
        rows: list[dict[str, Any]] = []
        if kind == "scorer":
            for rec in self.calibration_records(split):
                rows += [{"call_id": rec.call_id, "cell": rec.cell, "split": rec.split, "stage_index": j, "features": rec.feature_vectors[j],
                          "y": rec.labels[j], "el": rec.elig[j]} for j in range(len(rec.scores))]
        else:
            build = records.confusion_records if kind == "confusion" else records.probe_model_records
            for call_id in [r[0] for r in self.db.execute("SELECT call_id FROM gold WHERE split=? ORDER BY call_id", (split,))]:
                call = self._call(call_id)
                if call:
                    rows += build(call)
        return TrainingData(kind=kind, split=split, records=rows, ts=self.clock.now())

    def cell_stats(self, rungs_commit_rate: dict[str, list[float]] | None = None, since: dict[str, float] | None = None) -> CellStats:
        return stats.cell_stats(self.db, self.cat, self.clock.now(), rungs_commit_rate, since)

    def counts(self) -> dict[str, int]:
        """Finished calls per split."""
        return dict(self.db.execute("SELECT split, COUNT(*) FROM calibration GROUP BY split").fetchall())

    def advisory(self) -> dict[str, float | int]:
        return stats.advisory(self.db)

    def joined(self, split: str) -> list[dict[str, Any]]:
        """Calibration record and gold label of every call of a split, as C15 reads them. Never Test: that one goes through `test_split`."""
        custody.forbid_test(split)
        return self._joined(split)

    def test_split(self, caller: str, release_tag: str) -> TestSplit:
        rows = self._joined("Test")
        custody.serve(self.db, caller, release_tag, self.clock.now(), len(rows))
        return TestSplit(release_tag=release_tag, records=rows, ts=self.clock.now())

    def _joined(self, split: str) -> list[dict[str, Any]]:
        return [{"calibration": json.loads(c), "gold": json.loads(g)} for c, g in self.db.execute(
            "SELECT c.json, g.json FROM calibration c JOIN gold g USING (call_id) WHERE c.split=? ORDER BY c.call_id", (split,))]

    def labeled_outcomes(self, since_ts: float | None = None) -> list[LabeledOutcome]:
        return [LabeledOutcome.model_validate_json(r[0]) for r in self.db.execute(
            "SELECT json FROM outcomes WHERE ts>=? ORDER BY ts, call_id", (since_ts or 0.0,))]

    def close(self) -> None:
        self.db.close()

    # ---- internals
    def _gold(self, call_id: str) -> GoldLabel | None:
        row = self.db.execute("SELECT json FROM gold WHERE call_id=?", (call_id,)).fetchone()
        return GoldLabel.model_validate_json(row[0]) if row else None

    def _payloads(self, call_id: str) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM decisions WHERE call_id=? ORDER BY seq", (call_id,))]

    def _call(self, call_id: str) -> dict[str, Any] | None:
        gold, payloads = self._gold(call_id), self._payloads(call_id)
        scored = [p for p in payloads if p["collect"] is not None]
        if gold is None or not scored:
            return None
        stages = {p["stage_index"]: p for p in scored}
        return {"gold": gold, "stage0": stages[min(stages)], "stages": stages}

    def _finalize(self, call_id: str) -> None:
        """A call is final once both its gold label and its SESSION CLOSE are present, in either order."""
        gold = self._gold(call_id)
        closed = self.db.execute("SELECT closed_ts FROM sessions WHERE call_id=?", (call_id,)).fetchone()
        if gold is None or closed is None or self.db.execute("SELECT 1 FROM finalized WHERE call_id=?", (call_id,)).fetchone():
            return
        payloads = self._payloads(call_id)
        ts = self.db.execute("SELECT MIN(ts) FROM decisions WHERE call_id=?", (call_id,)).fetchone()[0] or closed[0]
        if (rec := join.trajectory(gold, payloads, ts)) is not None:
            self.db.execute("INSERT OR REPLACE INTO calibration VALUES (?,?,?,?,?)", (call_id, rec.cell, rec.split, ts, rec.model_dump_json()))
        out = join.live_commit(gold, payloads, ts)
        if out is not None:
            self._store_outcome(out)
        if self._call_cbs and (call := join.summary(gold, payloads, out)) is not None:
            for cb in self._call_cbs:
                cb(call)
        self.db.execute("INSERT INTO finalized VALUES (?)", (call_id,))

    def _store_outcome(self, out: LabeledOutcome) -> None:
        self.db.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?)", (out.call_id, out.label_tier, out.ts, out.model_dump_json()))
        for cb in self._outcome_cbs:
            cb(out)
