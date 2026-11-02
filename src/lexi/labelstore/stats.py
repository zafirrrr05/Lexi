"""Cell statistics (C14 step 5) and the advisory Tier-R metrics (10.4): statistics only, never certificate input."""

import json
import math
import sqlite3
from collections.abc import Mapping

from lexi.common.catalog import Catalog
from lexi.common.types import CellStat, CellStats


def cell_stats(db: sqlite3.Connection, cat: Catalog, now: float, commit_rate: Mapping[str, list[float]] | None = None,
               since: Mapping[str, float] | None = None) -> CellStats:
    """Deficit per cell and rung = n_target(alpha_r) - estimated accepted calls (n_records x commit rate at that rung). `since` is the shift onset
    of a cell under recertification: older records do not count (C6 step 11)."""
    out = {}
    for cell in cat.cells():
        n, newest = db.execute("SELECT COUNT(*), MAX(ts) FROM calibration WHERE split='Calib' AND cell=? AND ts>=?",
                               (cell, (since or {}).get(cell, float("-inf")))).fetchone()
        rates = (commit_rate or {}).get(cell, [0.5] * len(cat.rungs))
        est = [n * r for r in rates]
        need = [cat.n_target(cat.alpha_of(cell, k)) for k in range(len(cat.rungs))]
        out[cell] = CellStat(n_records=n, est_accept_by_rung=est, deficit_by_rung=[max(0, math.ceil(a - e)) for a, e in zip(need, est, strict=True)],
                             gold_label_age_s=None if newest is None else now - newest)
    return CellStats(cells=out, ts=now)


def advisory(db: sqlite3.Connection) -> dict[str, float | int]:
    """Residual wrong-post rate and false-undo rate (C9 note): what the receipt and the undo window did after the certified decision."""
    undone = {r[0] for r in db.execute("SELECT call_id FROM ledger_events WHERE event='COMPENSATED' AND reason LIKE 'undo%'")}
    posted = {r[0] for r in db.execute("SELECT call_id FROM ledger_events WHERE event='POSTED' AND provenance='autonomous'")}
    wrong = {r[0]: json.loads(r[1])["y_final"] for r in db.execute("SELECT call_id, json FROM outcomes WHERE tier='G'")}
    return {"undone": len(undone), "posted": len(posted),
            "false_undo": sum(1 for c in undone if wrong.get(c) == 0), "residual_wrong_posts": sum(1 for c in posted if wrong.get(c) == 1)}
