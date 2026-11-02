"""C5 step 4: training produces a monotone, calibrated, loadable scorer bundle (synthetic rows, no providers)."""

import math
import random

import pytest

from lexi.common.errors import RegistryError
from lexi.risk.bundle import ScorerBundle
from lexi.risk.calibrate import ece
from lexi.risk.features import NAMES
from lexi.risk.train import train


def _rows(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        p = rng.uniform(0.3, 1.0)
        mc = min(1.0, max(0.0, p + rng.gauss(0, 0.15)))
        f: dict[str, float | None] = {k: None for k in NAMES}
        f |= {"amount_p_top": p, "amount_margin": p - (1 - p) / 2, "amount_min_conf": mc, "amount_entropy": 1 - p, "amount_p_second": (1 - p) / 2,
              "amount_mean_conf": mc, "amount_confusable_mass": rng.uniform(0, 0.3), "plc_fraction": rng.uniform(0, 0.05), "snr_db": rng.uniform(0, 30)}
        risk = 1 - p * 0.9 + 0.5 * f["amount_confusable_mass"]
        rows.append({"call_id": f"c{i}", "cell": "hi.Q2.T1", "split": "Train", "stage_index": 0, "features": f, "y": int(rng.random() < risk), "el": 1})
    return rows


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> tuple[ScorerBundle, list[dict]]:
    valid = _rows(1500, 2)
    return train(_rows(3000, 1), valid, tmp_path_factory.mktemp("sc")), valid


def test_calibrated_on_valid(bundle: tuple[ScorerBundle, list[dict]]) -> None:
    b, valid = bundle
    s = b.score_features([r["features"] for r in valid])
    assert ece(s, [r["y"] for r in valid]) <= 0.02 + 1e-9  # isotonic is fitted on this split, so ECE target RC2 is met by construction
    assert all(1e-4 <= v <= 1.0 for v in s)


def test_monotone_in_p_top_and_confusable_mass(bundle: tuple[ScorerBundle, list[dict]]) -> None:
    b, valid = bundle
    base = dict(valid[0]["features"])
    lo = [b.score_features([{**base, "amount_p_top": p, "amount_margin": p - 0.1, "amount_min_conf": p}])[0] for p in (0.4, 0.6, 0.8, 0.95)]
    assert lo == sorted(lo, reverse=True)
    hi = [b.score_features([{**base, "amount_confusable_mass": m}])[0] for m in (0.0, 0.1, 0.2, 0.3)]
    assert hi == sorted(hi)


def test_vectorised_equals_single_and_roundtrip(bundle: tuple[ScorerBundle, list[dict]], tmp_path) -> None:
    b, valid = bundle
    rows = [r["features"] for r in valid[:50]]
    single = [b.score_row(r)[1] for r in rows]
    assert b.score_features(rows) == pytest.approx(single, abs=1e-12)
    b.save(tmp_path)
    again = ScorerBundle.load(tmp_path)
    assert again.version == b.version and again.score_features(rows) == b.score_features(rows)


def test_missing_values_are_scored_not_fatal(bundle: tuple[ScorerBundle, list[dict]]) -> None:
    b, _ = bundle
    s = b.score_features([{k: None for k in NAMES}])[0]
    assert 1e-4 <= s <= 1.0 and not math.isnan(s)


def test_corrupt_bundle_raises(tmp_path) -> None:
    with pytest.raises(RegistryError):
        ScorerBundle.load(tmp_path)


def test_single_class_labels_rejected(tmp_path) -> None:
    rows = [{**r, "y": 0} for r in _rows(50, 3)]
    with pytest.raises(ValueError):
        train(rows, rows, tmp_path)
