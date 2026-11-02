"""CERT-v1 validity (10.5 a): over repeated Calib draws the share of certificates whose true selective risk exceeds alpha stays below delta,
for a calibrated scorer and for scorers that under-estimate risk. The score is untrusted; the test is trusted."""

from types import SimpleNamespace

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.stats import beta

from lexi.authority.cert_v1 import certify
from lexi.authority.grid import design
from lexi.common.catalog import load_catalog, n_needed

CAT = load_catalog()
ALPHA, DELTA, N_CALIB = 0.02, 0.025, 2000
N_TARGET = n_needed(ALPHA, DELTA, 2)


def draw(rng: np.random.Generator, n: int, k: float) -> list[SimpleNamespace]:
    """Single-stage trajectories. Plain namespaces instead of CalibrationRecord: certify() reads three fields, and 600k pydantic models cost 15 s."""
    s = rng.beta(0.5, 8, n)
    y = rng.random(n) < np.minimum(1.0, k * s)
    return [SimpleNamespace(scores=[float(s[i])], elig=[1], labels=[int(y[i])]) for i in range(n)]


def true_risk(lam: float, k: float) -> float:
    """R(lambda) = P(y = 1 | s <= lambda) under the simulated world, by numerical integration."""
    num = quad(lambda s: min(1.0, k * s) * beta.pdf(s, 0.5, 8), 0, lam, limit=200)[0]
    return num / beta.cdf(lam, 0.5, 8)


@pytest.mark.parametrize("k", [1.0, 1.6, 2.5])
def test_violations_stay_below_delta(k):
    rng = np.random.default_rng(int(k * 100))
    grid = design(draw(rng, 5000, k), ALPHA, N_CALIB, N_TARGET, CAT.grid)  # fixed before any Calib draw
    assert grid
    risk = {lam: true_risk(lam, k) for lam in grid}
    trials, certified, violations = 200, 0, 0
    for _ in range(trials):
        res = certify(draw(rng, N_CALIB, k), grid, ALPHA, DELTA)
        if res.lam is not None:
            certified += 1
            violations += risk[res.lam] > ALPHA
    assert certified > 0.3 * trials  # the test has power: a worse scorer lowers autonomy, it does not break validity
    assert violations / trials <= DELTA
