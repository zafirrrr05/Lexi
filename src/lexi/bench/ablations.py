"""Ablations (16.4): each component has to earn its place. A1, A2 and A7 retrain the scorer without a feature group and compare wrong-commit rates at the
autonomy of the full scorer; A6 takes the hard limits away. A3-A5 and A8 need live runs: the caller passes their results in or they are reported as not run."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from lexi.bench.metrics import bootstrap
from lexi.bench.replay import Outcome, Traj, rate, replay
from lexi.common.catalog import Catalog
from lexi.risk.features import CHANNEL, NAMES, SLOTS
from lexi.risk.train import train

VARIANTS = {
    "A1": ("no ledger prior", [n for n in NAMES if n.endswith("prior_log_odds_top")]),
    "A2": ("top-1 transcript, no lattice", [f"{s}_{f}" for s in SLOTS for f in ("p_second", "margin", "entropy", "confusable_mass")]),
    "A7": ("no channel features", list(CHANNEL)),
}
LIVE_ONLY = {"A3": "yes/no probes only: needs a caller model run with the probe planner restricted",
             "A4": "no sentinel: needs a shift run with the sentinel's effect on the registry switched off",
             "A5": "Evon emits values: needs the live parser", "A8": "fixed endpointing: needs a run with gateway.numeric_hold off"}


def rescored(trajs: Sequence[Traj], bundle: Any) -> list[Traj]:
    """The same trajectories with every stage's score recomputed by another scorer from the stored feature vectors."""
    return [t._replace(scores=np.array(bundle.score_features(t.fv))) for t in trajs]


def match_lam(trajs: Sequence[Traj], target: Mapping[str, float]) -> dict[str, float | None]:
    """Per cell the loosest lambda whose autonomy does not exceed `target[cell]`: no labels are consulted, so this is a fair way to equalise coverage.
    ponytail: bisection over the cell's own scores; autonomy is monotone in lambda."""
    lam: dict[str, float | None] = {}
    for cell in {t.cell for t in trajs}:
        mine = [t for t in trajs if t.cell == cell]
        grid = np.unique(np.concatenate([t.scores for t in mine]))
        lo, hi = 0, len(grid) - 1
        best: float | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if rate(replay(mine, {cell: float(grid[mid])}))[0] <= target.get(cell, 0.0):
                best, lo = float(grid[mid]), mid + 1
            else:
                hi = mid - 1
        lam[cell] = best
    return lam


def autonomy_by_cell(out: Sequence[Outcome]) -> dict[str, float]:
    cells = {o.t.cell for o in out}
    return {c: rate([o for o in out if o.t.cell == c])[0] for c in cells}


def _compare(full: Sequence[Outcome], variant: Sequence[Outcome], rng: np.random.Generator) -> dict[str, float]:
    by_id = {o.t.call_id: o for o in variant}

    def diff(sel: list[Outcome]) -> float:
        return rate([by_id[o.t.call_id] for o in sel])[1] - rate(sel)[1]

    lo, hi = bootstrap(list(full), lambda o: o.t.script_id, diff, rng)
    return {"full_autonomy": rate(full)[0], "variant_autonomy": rate(variant)[0], "full_wrong_rate": rate(full)[1], "variant_wrong_rate": rate(variant)[1],
            "wrong_rate_increase": diff(list(full)), "increase_lo": lo, "increase_hi": hi}


def run(test: Sequence[Traj], lam: Mapping[str, float | None], cat: Catalog, train_rows: Sequence[dict[str, Any]], valid_rows: Sequence[dict[str, Any]],
        workdir: Path, rng: np.random.Generator, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    full = replay(test, lam)
    target = autonomy_by_cell(full)
    out: dict[str, Any] = {}
    for key, (what, drop) in VARIANTS.items():
        bundle = train(train_rows, valid_rows, workdir / key, drop_features=drop)
        again = rescored(test, bundle)
        out[key] = {"what": what, **_compare(full, replay(again, match_lam(again, target)), rng)}
    cap = cat.limits.max_autonomous_amount_inr
    on, off = replay(test, lam, cap=cap), replay(test, lam)
    out["A6"] = {"what": "no hard limits", "max_loss_with_limits": max((o.t.amount for o in on if o.wrong and o.stage is not None), default=0),
                 "max_loss_without": max((o.t.amount for o in off if o.wrong and o.stage is not None), default=0)}
    for key, why in LIVE_ONLY.items():
        out[key] = {"what": why, **(extra or {}).get(key, {"status": "not run"})}
    return out
