"""CERT-v1 (10.2-10.3): fixed-sequence exact-binomial certification of one cell and rung over COLLECT trajectories.
Valid for any score function: the only assumption is exchangeability of Calib with deployment (EXCH)."""

from collections.abc import Sequence
from typing import Any, NamedTuple

import numpy as np
from scipy.stats import binom

from lexi.common.types import CalibrationRecord


def _padded(rows: list[list[Any]], fill: float) -> "np.ndarray[Any, Any]":
    width = max((len(r) for r in rows), default=1)
    return np.array([r + [fill] * (width - len(r)) for r in rows]).reshape(len(rows), width)


class Trajectories:
    """The (s_j, el_j, y_j) trajectories of one cell as arrays, so n(lambda) and e(lambda) cost a few vector operations per grid point."""

    def __init__(self, records: Sequence[CalibrationRecord]) -> None:
        self.s = _padded([r.scores for r in records], np.inf)  # padding stages never commit: s = inf, el = 0
        self.el = _padded([r.elig for r in records], 0) == 1
        self.y = _padded([r.labels for r in records], 0)

    def counts(self, lam: float) -> tuple[int, int]:
        """(n(lambda), e(lambda)): calls that commit at lambda and how many of those are materially wrong. j* = first stage with el_j = 1 and s_j <= lambda."""
        ok = self.el & (self.s <= lam)
        commits = ok.any(axis=1)
        j_star = ok.argmax(axis=1)
        return int(commits.sum()), int(self.y[np.arange(len(j_star)), j_star][commits].sum())


class Point(NamedTuple):
    lam: float
    n: int
    errors: int
    p: float


class CertResult(NamedTuple):
    lam: float | None  # last certified grid point; None = rung absent
    n: int
    errors: int
    p: float | None
    points: list[Point]  # the walk up the grid, for the certificate board


def certify(records: Sequence[CalibrationRecord], grid: Sequence[float], alpha_r: float, delta: float) -> CertResult:
    """Walk the grid upward, certify while p(lambda) = BinomialCDF(e; n, alpha_r) <= delta, stop at the first failure (10.3)."""
    traj = Trajectories(records)
    points: list[Point] = []
    best: Point | None = None
    for lam in grid:
        n, e = traj.counts(lam)
        points.append(Point(lam, n, e, float(binom.cdf(e, n, alpha_r)) if n else 1.0))
        if points[-1].p > delta:
            break
        best = points[-1]
    return CertResult(best.lam if best else None, best.n if best else 0, best.errors if best else 0, best.p if best else None, points)
