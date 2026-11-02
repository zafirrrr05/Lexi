"""Probe response models (C8, K12): fit probe_models.json from the Train split's answered probes.

Each rate is a beta posterior mean whose prior (PRIOR_N pseudo-answers at the default rate) keeps a small sample from producing 0 or 1.
gamma is not fitted: the TrainingData records carry no posteriors to fit an exponent against, so it stays at its documented default."""

from collections.abc import Iterable
from typing import Any

from lexi.common.hashing import digest
from lexi.risk.probe_update import ProbeParams

PRIOR_N = 20


def _rate(hits: int, total: int, default: float) -> float:
    return (hits + default * PRIOR_N) / (total + PRIOR_N)


def _unclear(rows: list[dict[str, Any]], kind: str, default: float) -> float:
    of_kind = [r for r in rows if r["probe_kind"] == kind]
    return _rate(sum(r["answer_kind"] == "UNCLEAR" for r in of_kind), len(of_kind), default)


def fit(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    d = ProbeParams()
    choice = [r for r in rows if r["probe_kind"] in ("CONTRAST", "INTENT_CONTRAST") and r["answer_kind"] == "CHOICE" and r["options_has_gold"]]
    readback = [r for r in rows if r["probe_kind"] == "READBACK" and r["answer_kind"] in ("YES", "NO")]
    right = [r for r in readback if r["options_has_gold"]]
    wrong = [r for r in readback if not r["options_has_gold"]]
    out: dict[str, Any] = {
        "eps_c": _rate(sum(not r["answer_correct"] for r in choice), len(choice), d.eps_c),
        "eta": _rate(sum(r["answer_kind"] == "NO" for r in right), len(right), d.eta),
        "kappa": _rate(sum(r["answer_kind"] == "YES" for r in wrong), len(wrong), d.kappa),
        "gamma": d.gamma,
        "p_unclear": {k: _unclear(rows, k, v) for k, v in d.p_unclear.items()},
        "p_silence": _rate(sum(r["answer_kind"] == "SILENCE" for r in rows), len(rows), d.p_silence)}
    out["version"] = "probe-" + digest(out)[:10]
    return out
