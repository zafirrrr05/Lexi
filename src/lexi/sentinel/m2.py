"""Detector M2 (C10): gold-labeled outcomes of autonomous commits against the contract alpha_0 (the cell's RG0 alpha).
Under risk <= alpha_0, E[g_t] <= 1, so ARL(c) >= c labeled commits (10.5 b)."""

from collections.abc import Sequence

import numpy as np


class M2:
    def __init__(self, alpha0: float, nu_grid: Sequence[float]) -> None:
        # a factor 1 + nu (e - alpha0) must stay >= 0 for a bet to be valid; at alpha0 = 0.10 the nu = 16 bet could lose more than the stake
        self.nu = np.asarray([v for v in nu_grid if v * alpha0 < 1.0], dtype=float)
        self.alpha0 = alpha0
        self.sr = 0.0
        self.factor = 1.0  # the latest betting factor, read by the state machine to date the onset of a shift

    def update(self, e: int) -> float:
        self.factor = float(np.mean(1.0 + self.nu * (e - self.alpha0)))
        self.sr = (1.0 + self.sr) * self.factor
        return self.sr

    def reset(self) -> None:
        self.sr = 0.0
