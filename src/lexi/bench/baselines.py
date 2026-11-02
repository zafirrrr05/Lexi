"""Baselines (16.3). B1 commits when the weakest word of the critical spans is confident enough, B2 when the parser's own confidence is, B3 when the
calibrated score is below alpha/2 with no test. B1 and B2 are tuned on Valid; none of them probes, so they read stage 0 only."""

from collections.abc import Callable, Mapping, Sequence

import numpy as np

from lexi.bench.metrics import bootstrap
from lexi.bench.replay import Outcome, Traj, rate, replay
from lexi.common.catalog import Catalog
from lexi.risk.features import SLOTS


def min_conf_critical(fv: Mapping[str, float | None]) -> float | None:
    """The weakest Prisma word confidence over the critical slots, read from a stored feature vector."""
    seen = [v for s in SLOTS if (v := fv.get(f"{s}_min_conf")) is not None]
    return min(seen) if seen else None


def evon_confidence(fv: Mapping[str, float | None]) -> float | None:
    """B2's signal. Evon's own number is not stored; the intent posterior of stage 0 is what the parser hands on, so that is the stand-in (docs/DECISIONS.md)."""
    return fv.get("intent_p_top")


def match_theta(signals: Sequence[float | None], autonomy: float) -> float:
    """theta such that the share of calls with signal >= theta equals `autonomy` (matched-autonomy comparison, 16.3). A call without a signal never commits."""
    k = round(autonomy * len(signals))
    if k <= 0:
        return 1.01
    return sorted((-1.0 if s is None else s for s in signals), reverse=True)[min(k, len(signals)) - 1]


def theta_for_risk(signals: Sequence[float | None], y: Sequence[int], risk: float) -> float:
    """The loosest theta whose wrong-commit rate on these calls stays within `risk` (matched-risk comparison)."""
    order = np.argsort([1.0 if s is None else -s for s in signals], kind="stable")  # strongest signal first, a missing one last
    wrong = np.cumsum(np.asarray(y)[order])
    ok = np.nonzero(wrong / np.arange(1, len(order) + 1) <= risk)[0]
    return 1.01 if ok.size == 0 else sorted((-1.0 if s is None else s for s in signals), reverse=True)[int(ok.max())]


def commits_at(trajs: Sequence[Traj], signal: Callable[[Mapping[str, float | None]], float | None], theta: float) -> list[Outcome]:
    out = []
    for t in trajs:
        s = signal(t.fv[0])
        out.append(Outcome(t, 0 if s is not None and s >= theta else None, int(t.labels[0]) if s is not None and s >= theta else 0))
    return out


def compare(test: Sequence[Traj], valid: Sequence[Traj], lam: Mapping[str, float | None], cat: Catalog, rng: np.random.Generator) -> dict[str, dict[str, float]]:
    """LEXI against B1-B3 on Test: at matched autonomy (their wrong-commit rate, and LEXI's divided by theirs with a bootstrap interval over scripts)
    and at matched risk (their autonomy). Thresholds come from Valid only."""
    v_auto, v_risk = rate(replay(valid, lam))
    out: dict[str, dict[str, float]] = {}
    lexi = replay(test, lam)
    for name, signal in (("B1", min_conf_critical), ("B2", evon_confidence)):
        theta = match_theta([signal(t.fv[0]) for t in valid], v_auto)
        theta_r = theta_for_risk([signal(t.fv[0]) for t in valid], [int(t.labels[0]) for t in valid], v_risk)
        out[name] = _row(lexi, commits_at(test, signal, theta), commits_at(test, signal, theta_r), rng)
    half = {c: cat.alpha_of(c, 0) / 2 for c in {t.cell for t in test}}  # B3: s <= alpha/2, the same threshold for the cell whatever the data say
    b3 = replay([t._replace(scores=t.scores[:1], elig=np.ones(1, dtype=int)) for t in test], half)
    out["B3"] = _row(lexi, b3, b3, rng)
    return out


def _row(lexi: Sequence[Outcome], matched_auto: Sequence[Outcome], matched_risk: Sequence[Outcome], rng: np.random.Generator) -> dict[str, float]:
    by_id = {o.t.call_id: o for o in matched_auto}

    def ratio(sel: list[Outcome]) -> float:
        a, b = rate(sel)[1], rate([by_id[o.t.call_id] for o in sel])[1]
        return a / b if b else float("nan")

    lo, hi = bootstrap(list(lexi), lambda o: o.t.script_id, ratio, rng)
    return {"autonomy": rate(matched_auto)[0], "wrong_rate": rate(matched_auto)[1], "ratio": ratio(list(lexi)), "ratio_lo": lo, "ratio_hi": hi,
            "matched_risk_autonomy": rate(matched_risk)[0]}
