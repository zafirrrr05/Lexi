"""Stationarity check before issuing (C6 step 4): is the Calib window itself exchangeable in time order?"""

from collections.abc import Mapping, Sequence
from typing import Any

from lexi.common.types import CalibrationRecord
from lexi.sentinel.m1 import M1


def check(records: Sequence[CalibrationRecord], cell: str, cfg: Mapping[str, Any]) -> tuple[bool, float]:
    """M1 over the first-pass scores in time order, first half as the reference. Returns (stationary, peak SRM1)."""
    scores = [r.scores[0] for r in sorted(records, key=lambda r: (r.ts, r.call_id))]
    half = len(scores) // 2
    m1 = M1(cfg["m1"]["eps_grid"], f"{cfg['seed']}:{cell}:stationarity", scores[:half])
    peak = max((m1.update(s) for s in scores[half:]), default=0.0)
    return peak < cfg["stationarity_threshold"], peak
