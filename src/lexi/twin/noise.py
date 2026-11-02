"""Synthetic noises (C11 step 4): filtered noise only, so no external licence is needed. All return unit power."""

from collections.abc import Callable

import numpy as np
from scipy.signal import butter, lfilter

BabbleSource = Callable[[int], np.ndarray]
KINDS = ("none", "babble", "market", "traffic", "rain")


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.sqrt(np.mean(x ** 2) + 1e-12)


def _filtered(rng: np.random.Generator, n: int, kind: str, hz: float) -> np.ndarray:
    b, a = butter(2, hz / 4000, btype=kind)
    return lfilter(b, a, rng.standard_normal(n))


def _syllabic(rng: np.random.Generator, n: int) -> np.ndarray:
    """Speech-like modulation (3-6 Hz) over band-limited noise: the fallback babble voice."""
    t = np.arange(n) / 8000
    return _filtered(rng, n, "low", 3000) * (1 + np.sin(2 * np.pi * rng.uniform(3, 6) * t + rng.uniform(0, 6.3)))


def make_noise(kind: str, n: int, rng: np.random.Generator, babble_source: BabbleSource | None = None) -> np.ndarray:
    if kind == "none":
        x = rng.standard_normal(n)
    elif kind == "babble":  # several overlapping talkers
        src = babble_source or (lambda m: _syllabic(rng, m))
        x = sum(np.roll(src(n), int(rng.integers(0, n))) for _ in range(5))
    elif kind == "market":  # low murmur plus occasional clinks
        x = _filtered(rng, n, "low", 900) * 3
        for at in rng.integers(0, max(n - 200, 1), size=max(1, n // 4000)):
            x[at:at + 200] += _filtered(rng, 200, "high", 2500) * 4 * np.exp(-np.arange(200) / 40)
    elif kind == "traffic":  # rumble that swells and fades
        x = _filtered(rng, n, "low", 400) * (1.2 + np.sin(2 * np.pi * rng.uniform(0.2, 0.6) * np.arange(n) / 8000))
    elif kind == "rain":
        x = _filtered(rng, n, "high", 1200)
    else:
        raise ValueError(f"unknown noise kind {kind!r}")
    return _unit(np.asarray(x, dtype=np.float64))
