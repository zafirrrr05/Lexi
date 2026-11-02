"""Credit governor (C11 step 9): a budget file {credits_total, reserve 10%, per-call cost EWMA} and the allocation order
Calib deficit > Test > Valid > Train. Provider responses are cached, so a re-run costs nothing; only fresh audio is charged."""

import json
import os
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from lexi.common.types import CellStats

ORDER = ("Test", "Valid", "Train")  # after Calib deficits
INITIAL_COST = 0.5  # credits per call until the first one has been measured


class Governor:
    def __init__(self, path: Path, total: float, reserve: float = 0.10, smoothing: float = 0.2) -> None:
        self.path, self.total, self.reserve, self.smoothing = path, total, reserve, smoothing
        state = json.loads(path.read_text()) if path.exists() else {}
        self.spent: float = state.get("spent", 0.0)
        self.cost: float = state.get("cost_per_call", INITIAL_COST)

    @property
    def usable(self) -> float:
        return self.total * (1 - self.reserve) - self.spent

    def can_afford(self, calls: int = 1) -> bool:
        return self.usable >= calls * self.cost

    def charge(self, credits: float) -> None:
        self.spent += credits
        self.cost += self.smoothing * (credits - self.cost)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"credits_total": self.total, "reserve": self.reserve, "spent": self.spent, "cost_per_call": self.cost}))
        os.replace(tmp, self.path)

    def next_call(self, stats: CellStats | None, produced: Counter[str], targets: Mapping[str, int]) -> tuple[str, str | None] | None:
        """(split, cell) of the next call, or None when nothing is open or nothing is affordable. A Calib deficit comes first, the cell that gains the most
        estimated accepted calls per raw call first (4.7), within the Calib budget `targets["Calib"]`; then Test, Valid, Train until each reaches its target;
        those carry no cell, the RTP composition decides."""
        if not self.can_afford():
            return None
        if stats is not None and produced["Calib"] < targets.get("Calib", 0):  # the Calib budget bounds a cell that never reaches n_target (4.7)
            open_ = {c: s for c, s in stats.cells.items() if s.deficit_by_rung[0] > 0}
            if open_:
                rate = lambda c: open_[c].est_accept_by_rung[0] / open_[c].n_records if open_[c].n_records else 0.5  # noqa: E731
                return "Calib", max(open_, key=lambda c: (rate(c), open_[c].deficit_by_rung[0], c))
        return next(((s, None) for s in ORDER if produced[s] < targets.get(s, 0)), None)
