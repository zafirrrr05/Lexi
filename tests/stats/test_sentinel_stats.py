"""C10 statistics through the real M1/M2 code (10.5 b, c; RC5): false-alarm run lengths exceed their thresholds, detection delays meet the MUST medians."""

import random
import statistics

import numpy as np
import pytest

from lexi.common.catalog import load_yaml
from lexi.sentinel.m1 import M1
from lexi.sentinel.m2 import M2

CFG = load_yaml("detectors.yaml")
ALPHA0 = 0.02


def run_length(det_update, threshold: float, cap: int) -> tuple[int, bool]:
    for t in range(1, cap + 1):
        if det_update() >= threshold:
            return t, True
    return cap, False


def m1_run(m1: M1, scores, threshold: float) -> tuple[int, bool]:
    it = iter(scores)
    return run_length(lambda: m1.update(float(next(it))), threshold, len(scores))


def m2_stream(risk: float, seed: int, threshold: float, cap: int) -> tuple[int, bool]:
    rng, m2 = random.Random(seed), M2(ALPHA0, CFG["m2"]["nu_grid"])
    return run_length(lambda: m2.update(int(rng.random() < risk)), threshold, cap)


def test_m1_null_run_length_exceeds_threshold():
    ref_rng = np.random.default_rng(1)
    lengths, alarms = 0, 0
    for run in range(30):
        m1 = M1(CFG["m1"]["eps_grid"], f"null:{run}", ref_rng.beta(0.5, 8, 300))
        n, hit = m1_run(m1, np.random.default_rng(100 + run).beta(0.5, 8, 20_000), 1e3)
        lengths, alarms = lengths + n, alarms + hit
    assert lengths / max(alarms, 1) >= 1e3  # ARL(c) >= c; published 5,256 (10.5 b)


@pytest.mark.parametrize("threshold,published", [(1e3, 1642), (1e4, 18101)])
def test_m2_null_run_length_exceeds_threshold(threshold, published):
    runs = [m2_stream(ALPHA0, s, threshold, 40_000) for s in range(40)]
    arl = sum(n for n, _ in runs) / max(sum(h for _, h in runs), 1)
    assert arl >= threshold, f"ARL {arl:.0f} (published {published})"


@pytest.mark.parametrize("risk,limit", [(0.04, 700), (0.06, 200), (0.10, 70), (0.25, 30)])
def test_m2_detection_delay_median_rc5(risk, limit):
    delays = [m2_stream(risk, 10_000 + s, 1e4, 20_000)[0] for s in range(1000 if risk == 0.04 else 300)]
    assert statistics.median(delays) <= limit  # RC5: ALERT level c = 1e4, labeled commits


def test_m1_shift_is_detected_quickly():
    m1 = M1(CFG["m1"]["eps_grid"], "shift", np.random.default_rng(2).beta(0.5, 8, 500))
    n, hit = m1_run(m1, np.random.default_rng(3).beta(2.0, 6, 5000), 1e4)  # riskier than the reference: scores move up
    assert hit and n < 300
