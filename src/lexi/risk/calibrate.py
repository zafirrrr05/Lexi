"""Isotonic calibration (C5 step 4): s_raw -> s on the Valid split, plus expected calibration error."""

from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression

S_FLOOR = 1e-4


def fit_isotonic(raw: Sequence[float] | np.ndarray, y: Sequence[int]) -> dict[str, list[float]]:
    iso = IsotonicRegression(y_min=S_FLOOR, y_max=1.0, out_of_bounds="clip").fit(raw, y)
    return {"x": [float(v) for v in iso.X_thresholds_], "y": [float(v) for v in iso.y_thresholds_]}


def apply_isotonic(m: dict[str, Any], raw: np.ndarray) -> np.ndarray:
    return np.asarray(np.clip(np.interp(raw, m["x"], m["y"]), S_FLOOR, 1.0))


def ece(probs: Sequence[float] | np.ndarray, labels: Sequence[int], bins: int = 10) -> float:
    p, y = np.asarray(probs, dtype=float), np.asarray(labels, dtype=float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).sum() for b in range(bins) if (idx == b).any()) / max(len(p), 1))
