"""Detector M1 (C10): label-free, score-rank exchangeability. A smoothed conformal p-value per first-pass score, a betting factor over an
eps grid, and the Shiryaev-Roberts recursion. Under exchangeability the p-values are i.i.d. uniform, so ARL(c) >= c (10.5 b)."""

import random
from collections.abc import Iterable, Sequence

import numpy as np
from sortedcontainers import SortedList

P_FLOOR = 1e-12  # theta = 0 with an empty tail would give p = 0 and an infinite factor


class M1:
    def __init__(self, eps_grid: Sequence[float], seed: str, reference: Iterable[float] = ()) -> None:
        self.eps = np.asarray(eps_grid, dtype=float)
        self.rng = random.Random(seed)  # theta draws; seeded per cell so the detector replays from the audit stream
        self.ref: SortedList[float] = SortedList(reference)
        self.sr = 0.0
        self.factor = 1.0  # the latest betting factor, read by the state machine to date the onset of a shift

    def p_value(self, s: float) -> float:
        hi = self.ref.bisect_right(s)
        greater, equal = len(self.ref) - hi, hi - self.ref.bisect_left(s)
        return max((greater + self.rng.random() * (equal + 1)) / (len(self.ref) + 1), P_FLOOR)

    def update(self, s: float) -> float:
        """One first-pass score of the cell. Small p = riskier than history."""
        p = self.p_value(s)
        self.factor = float(np.mean(self.eps * p ** (self.eps - 1.0)))
        self.sr = (1.0 + self.sr) * self.factor
        return self.sr

    def grow(self, s: float) -> None:
        """The reference grows only while the cell is HEALTHY: a shift must not be absorbed into its own reference."""
        self.ref.add(s)

    def reset(self, reference: Iterable[float] | None = None) -> None:
        self.sr = 0.0
        if reference is not None:
            self.ref = SortedList(reference)
