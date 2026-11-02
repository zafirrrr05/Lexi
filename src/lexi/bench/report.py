"""Report (C15 step 5): the only producer of published numbers. `evaluate` replays the sealed Test split, `build` judges every SLO row of section 11 and
`write` puts results/<tag>/report.json and report.md on disk. An SLO nobody measured is `pass: None`, never a guess; a release is LEXI-verified only if every MUST row passes."""

import json
import operator
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from lexi.bench import ablations, baselines
from lexi.bench.metrics import show
from lexi.bench.replay import curve, lam_from_registry, load, per_cell, replay, summarize
from lexi.common.catalog import Catalog
from lexi.common.types import EvalReport
from lexi.risk.calibrate import ece

OPS = {"<=": operator.le, ">=": operator.ge, "==": operator.eq}
inr = lambda x: f"₹{x:,.0f}"  # noqa: E731


def build(tag: str, groups: Mapping[str, Any], measured: Mapping[str, Any], slo: Mapping[str, Mapping[str, Any]]) -> EvalReport:
    rows = []
    for key, spec in slo.items():
        v = measured.get(spec["metric"])
        ok = None if v is None else bool(OPS[spec["op"]](v, spec["value"]))
        rows.append({"id": key, "cls": spec["cls"], "metric": spec["metric"], "op": spec["op"], "target": spec["value"], "value": v, "pass": ok, "desc": spec["desc"]})
    return EvalReport(release_tag=tag, metrics=dict(groups), slo_table=rows, passed=all(r["pass"] is True for r in rows if r["cls"] == "MUST"))


def markdown(r: EvalReport) -> str:
    mark = {True: "pass", False: "FAIL", None: "not measured"}
    lines = [f"# LEXI report: {r.release_tag}", "", "LEXI-verified: **" + ("yes" if r.passed else "no") + "** (every MUST row has to pass)", "",
             "| id | class | criterion | target | observed | |", "|---|---|---|---|---|---|"]
    lines += [f"| {x['id']} | {x['cls']} | {x['desc']} | {x['op']} {x['target']} | {x['value']} | {mark[x['pass']]} |" for x in r.slo_table]
    for group, values in r.metrics.items():
        lines += ["", f"## {group}", ""] + [f"- {k}: {v}" for k, v in values.items()]
    return "\n".join(lines) + "\n"


def write(r: EvalReport, results_dir: Path) -> Path:
    out = results_dir / r.release_tag
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(r.model_dump(mode="json"), indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(markdown(r), encoding="utf-8")
    return out


def evaluate(tag: str, test_rows: Sequence[Mapping[str, Any]], valid_rows: Sequence[Mapping[str, Any]], train_scorer: Sequence[dict[str, Any]],
             valid_scorer: Sequence[dict[str, Any]], registry: Mapping[str, Any], cat: Catalog, slo: Mapping[str, Mapping[str, Any]], workdir: Path,
             extra: Mapping[str, Any] | None = None, seed: int = 20261004) -> EvalReport:
    """E1: the replay of the sealed Test trajectories at the certified lambdas. `extra` carries what only a live run can measure:
    {"measured": {metric: value}, "groups": {name: {...}}, "ablations": {A3: {...}}}."""
    extra = extra or {}
    rng = np.random.default_rng(seed)
    test, valid = load(test_rows), load(valid_rows)
    lam = lam_from_registry(registry)
    out = replay(test, lam)
    s, cells = summarize(out, cat), per_cell(out, cat, registry)
    base = baselines.compare(test, valid, lam, cat, rng)
    abl = ablations.run(test, lam, cat, train_scorer, valid_scorer, workdir, rng, extra.get("ablations"))
    first = [t for t in valid if t.elig[0] == 1]
    # Valid was collected before the scorer existed, so its stored scores belong to the provisional model; the figure of RC2 is the one the scorer recorded when it was fitted
    ece_valid = extra.get("valid_ece")
    if ece_valid is None:
        ece_valid = ece([float(t.scores[0]) for t in first], [int(t.labels[0]) for t in first])
    first_test = [t for t in test if t.elig[0] == 1]
    ece_test = ece([float(t.scores[0]) for t in first_test], [int(t.labels[0]) for t in first_test])  # D-011: Valid is in-sample for the isotonic map, Test is not
    t1q1 = [o for o in out if o.t.cell.split(".")[1:] == ["Q1", "T1"]]
    measured = {"cert_failed_cells": sum(not c["valid"] for c in cells.values()), "ece_valid": ece_valid, "probes_per_commit": s["probes_per_commit"],
                "autonomy_t1_q1": sum(o.stage in (0, 1) for o in t1q1) / len(t1q1) if t1q1 else None, "wrong_rate_ratio_vs_b1": base["B1"]["ratio"],
                **extra.get("measured", {})}
    groups: dict[str, Any] = {
        "run": {"Test calls": s["n"], "Valid calls": len(valid), "registry": f"v{registry['version']}, scorer {registry['scorer_version']}", "seed": seed},
        "certificates": {"cells certified": f"{sum(c['certified'] for c in cells.values())} of {len(cells)}",
                         "cells failing on Test": measured["cert_failed_cells"], "ECE on Valid": round(ece_valid, 4),
                         "ECE on Test, stage 0": round(ece_test, 4)},
        "autonomy": {"autonomy rate": s["autonomy"], "wrong-commit rate": show(s["wrong_rate"], *s["wrong_ci"], digits=4), "commits": s["commits"], "wrong commits": s["errors"],
                     "silent-error interception": round(s["interception_rate"], 4), "probes per commit": round(s["probes_per_commit"], 3),
                     "probe resolution rate": round(s["resolution_rate"], 3), "escalation rate": round(s["escalation_rate"], 3),
                     "CELB-cap (bound)": inr(s["celb_cap"]), "CELB-est (estimate)": inr(s["celb_est"]), "amount-weighted wrong-post rate": round(s["amount_wrong_rate"], 4),
                     "largest single error": inr(s["max_loss"])},
        "baselines": {k: v for n, b in base.items() for k, v in {
            f"{n} wrong-commit rate at LEXI's autonomy": round(b["wrong_rate"], 4), f"{n} autonomy": round(b["autonomy"], 3),
            f"LEXI / {n} wrong-commit ratio": show(b["ratio"], b["ratio_lo"], b["ratio_hi"], digits=2),
            f"{n} autonomy at LEXI's risk": round(b["matched_risk_autonomy"], 3)}.items()},
        "ablations": {k: (f"{v['what']}: wrong rate {v['wrong_rate_increase']:+.4f} [{v['increase_lo']:+.4f}, {v['increase_hi']:+.4f}] at autonomy {v['variant_autonomy']:.3f}"
                          if "wrong_rate_increase" in v else " ".join(f"{a}={b}" for a, b in v.items())) for k, v in abl.items()},
        "per_cell": {c: f"{v['commits']} of {v['n']} committed, {v['errors']} wrong, Clopper-Pearson [{v['ci'][0]:.3f}, {v['ci'][1]:.3f}] against alpha {v['alpha']}: "
                        + ("no certificate" if not v["certified"] else "holds" if v["valid"] else "FAILS") for c, v in cells.items()},
        "risk_coverage": {c: [[round(x, 4) for x in p] for p in curve(test, c)] for c in cells},
        **extra.get("groups", {}),
    }
    return build(tag, groups, measured, slo)

