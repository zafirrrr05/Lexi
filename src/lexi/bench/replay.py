"""Replay engine (C15 step 2, 10.2): any policy of the LEXI kind is simulated exactly from a COLLECT trajectory. A call commits at the first stage j with
el_j = 1 and s_j <= lambda and is then judged by y_j; a call with no such stage escalates. Nothing here is a model of the system, it is the system's rule."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import numpy as np

from lexi.bench.metrics import clopper_pearson
from lexi.common.catalog import Catalog
from lexi.common.types import ScoredProposal
from lexi.gate.decide import DecideInput, decide


class Traj(NamedTuple):
    call_id: str
    script_id: str
    cell: str
    scores: np.ndarray
    elig: np.ndarray
    labels: np.ndarray
    fv: list[dict[str, float | None]]  # feature vector per stage, for the baselines and the re-scored ablations
    amount: int  # the gold amount: the rupees a wrong commit of this call puts at risk
    tags: list[str]


class Outcome(NamedTuple):
    t: Traj
    stage: int | None  # None = escalated
    wrong: int


def load(records: Iterable[Mapping[str, Any]]) -> list[Traj]:
    """TestSplit.records (or the same shape built from Valid): {"calibration": CalibrationRecord, "gold": GoldLabel}."""
    return [Traj(c["call_id"], g["script_id"], c["cell"], np.array(c["scores"]), np.array(c["elig"]), np.array(c["labels"]), c["feature_vectors"],
                 int(g["gold_slots"].get("amount") or 0), c["tags"]) for r in records for c, g in [(r["calibration"], r["gold"])]]


def lam_from_registry(registry: Mapping[str, Any], rung: int = 0) -> dict[str, float | None]:
    """lambda of the certificate of `rung` per cell; a cell without one never commits."""
    return {cell: next((r["lam"] for r in e["ladder"] if r["rung"] == rung), None) for cell, e in registry["cells"].items()}


def commit_stage(scores: np.ndarray, elig: np.ndarray, lam: float | None) -> int | None:
    if lam is None:
        return None
    ok = (elig == 1) & (scores <= lam)
    return int(ok.argmax()) if ok.any() else None


def replay(trajs: Sequence[Traj], lam: Mapping[str, float | None], *, cap: int | None = None) -> list[Outcome]:
    """`cap` is the hard limit of 4.5: a call whose amount exceeds it is escalated before any score is consulted (A6 switches it off)."""
    out = []
    for t in trajs:
        j = None if cap is not None and t.amount > cap else commit_stage(t.scores, t.elig, lam.get(t.cell))
        out.append(Outcome(t, j, int(t.labels[j]) if j is not None else 0))
    return out


def rate(out: Sequence[Outcome]) -> tuple[float, float]:
    """(autonomy, wrong-commit rate) of a set of outcomes."""
    commits = [o for o in out if o.stage is not None]
    return len(commits) / max(len(out), 1), sum(o.wrong for o in commits) / max(len(commits), 1)


def summarize(out: Sequence[Outcome], cat: Catalog) -> dict[str, Any]:
    """The numbers of 16.2 that a replay can produce. Rates carry a Clopper-Pearson interval; the rupee figures use the gold amount as exposure."""
    commits = [o for o in out if o.stage is not None]
    wrong = [o for o in commits if o.wrong]
    wrong_ids = {o.t.call_id for o in wrong}
    probed = [o for o in out if len(o.t.scores) > 1]
    first_wrong = [o for o in out if o.t.elig[0] == 1 and o.t.labels[0] == 1]  # calls whose stage-0 proposal was materially wrong
    cap = cat.limits.max_autonomous_amount_inr
    return {"n": len(out), "commits": len(commits), "errors": len(wrong), "autonomy": len(commits) / max(len(out), 1),
            "wrong_rate": len(wrong) / max(len(commits), 1), "wrong_ci": clopper_pearson(len(wrong), len(commits)),
            "escalation_rate": 1 - len(commits) / max(len(out), 1),
            "probes_per_commit": float(np.mean([o.stage for o in commits])) if commits else 0.0,
            "resolution_rate": sum(o.stage is not None for o in probed) / max(len(probed), 1),
            "interception_rate": 1 - sum(o.t.call_id in wrong_ids for o in first_wrong) / max(len(first_wrong), 1),
            "celb_cap": sum(cat.alpha_of(o.t.cell, 0) * cap for o in commits), "celb_est": sum(cat.alpha_of(o.t.cell, 0) * o.t.amount for o in commits),
            "amount_wrong_rate": sum(o.t.amount for o in wrong) / max(sum(o.t.amount for o in commits), 1),
            "max_loss": max((o.t.amount for o in wrong), default=0)}


def per_cell(out: Sequence[Outcome], cat: Catalog, registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Certificate validity (RC1): the RG0 certificate of a cell holds on Test unless the lower Clopper-Pearson bound of the Test error rate exceeds alpha."""
    cells: dict[str, dict[str, Any]] = {}
    for cell in sorted({o.t.cell for o in out}):
        mine = [o for o in out if o.t.cell == cell]
        commits = [o for o in mine if o.stage is not None]
        k = sum(o.wrong for o in commits)
        lo, hi = clopper_pearson(k, len(commits))
        certified = any(r["rung"] == 0 for r in registry["cells"].get(cell, {"ladder": []})["ladder"])
        cells[cell] = {"n": len(mine), "commits": len(commits), "errors": k, "ci": (lo, hi), "alpha": cat.alpha_of(cell, 0), "certified": certified,
                       "valid": not certified or lo <= cat.alpha_of(cell, 0)}
    return cells


def curve(trajs: Sequence[Traj], cell: str, points: int = 8) -> list[tuple[float, float, float]]:
    """Selective risk against coverage for one cell (16.2): (lambda, coverage, risk) along the quantiles of the cell's scores."""
    mine = [t for t in trajs if t.cell == cell]
    if not mine:
        return []
    grid = np.unique(np.quantile(np.concatenate([t.scores[t.elig == 1] for t in mine if t.elig.any()] or [np.array([1.0])]), np.linspace(0.1, 1, points)))
    return [(float(lam), *rate(replay(mine, {cell: float(lam)}))) for lam in grid]


def replay_decisions(payloads: Iterable[Mapping[str, Any]], cat: Catalog) -> tuple[int, int]:
    """RC7: decide() run again on the stored input must give the stored decision. (checked, mismatches)."""
    n = bad = 0
    for p in payloads:
        again = decide(DecideInput(sp=ScoredProposal.model_validate(p["sp"]), **p["decide_input"]), cat).model_dump(mode="json")
        if again["probe_request"]:
            again["probe_request"]["base_sp"] = p["sp"]["sp_id"]  # the audit stores the id, not a second copy of the proposal
        n, bad = n + 1, bad + (again != p["decision"])
    return n, bad
