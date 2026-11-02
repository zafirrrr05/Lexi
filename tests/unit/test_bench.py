"""C15 on hand-made trajectories: the commit rule of 10.2, the intervals, the baselines' thresholds, the SLO table and the ablation groups."""

import numpy as np
import pytest

from lexi.bench import ablations, baselines
from lexi.bench.live_run import latency
from lexi.bench.metrics import bootstrap, clopper_pearson
from lexi.bench.replay import Traj, commit_stage, lam_from_registry, per_cell, replay, summarize
from lexi.bench.report import build
from lexi.risk.features import NAMES


def traj(cid, scores, elig, labels, cell="hi.Q1.T1", amount=1000, script="s", fv0=None):
    return Traj(cid, script, cell, np.array(scores), np.array(elig), np.array(labels), [fv0 or {}] * len(scores), amount, [])


def test_clopper_pearson_matches_the_closed_form_at_zero_events():
    lo, hi = clopper_pearson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(1 - 0.025 ** (1 / 10))
    assert clopper_pearson(0, 0) == (0.0, 1.0)


def test_commit_stage_is_the_first_eligible_stage_under_lambda():
    s, el = np.array([0.5, 0.01, 0.001]), np.array([1, 0, 1])
    assert commit_stage(s, el, 0.6) == 0
    assert commit_stage(s, el, 0.1) == 2  # stage 1 is below lambda but ineligible
    assert commit_stage(s, el, 0.0001) is None and commit_stage(s, el, None) is None


def test_summary_counts_commits_wrong_commits_probes_and_the_hard_limit(catalog):
    trajs = [traj("a", [0.01], [1], [0]), traj("b", [0.5, 0.01], [1, 1], [1, 1], amount=3000), traj("c", [0.9], [1], [1]), traj("d", [0.01], [1], [0], amount=90000)]
    out = replay(trajs, {"hi.Q1.T1": 0.05})
    s = summarize(out, catalog)
    assert (s["commits"], s["errors"], s["probes_per_commit"]) == (3, 1, pytest.approx(1 / 3))
    assert s["max_loss"] == 3000 and s["interception_rate"] == 0.5  # of b and c, c was escalated and b was not
    capped = replay(trajs, {"hi.Q1.T1": 0.05}, cap=catalog.limits.max_autonomous_amount_inr)
    assert summarize(capped, catalog)["commits"] == 2
    cells = per_cell(out, catalog, {"cells": {"hi.Q1.T1": {"ladder": [{"rung": 0, "lam": 0.05}]}}})
    assert cells["hi.Q1.T1"]["certified"] and lam_from_registry({"cells": {"x": {"ladder": []}}}) == {"x": None}


def test_baseline_thresholds_hit_the_requested_autonomy_and_risk():
    signals = [0.9, 0.8, 0.7, 0.6, None]
    assert baselines.match_theta(signals, 0.4) == 0.8
    assert baselines.match_theta(signals, 0.0) > 1
    assert baselines.theta_for_risk(signals, [0, 0, 1, 1, 1], 0.0) == 0.8
    assert baselines.theta_for_risk(signals, [1, 1, 1, 1, 1], 0.0) > 1


def test_bootstrap_resamples_scripts_not_calls():
    items = [(f"s{i // 4}", i % 2) for i in range(40)]
    lo, hi = bootstrap(items, lambda x: x[0], lambda xs: float(np.mean([v for _, v in xs])), np.random.default_rng(1))
    assert lo <= 0.5 <= hi


def test_unmeasured_slo_rows_are_none_and_block_verification():
    slo = {"R1": {"cls": "MUST", "metric": "a", "op": "<=", "value": 1, "desc": ""}, "R2": {"cls": "TARGET", "metric": "b", "op": ">=", "value": 0.6, "desc": ""}}
    r = build("t", {}, {"a": 0}, slo)
    assert [x["pass"] for x in r.slo_table] == [True, None] and r.passed
    assert not build("t", {}, {"a": 2}, slo).passed and not build("t", {}, {}, slo).passed


def test_ablation_groups_name_real_features_and_the_matched_lambda_does_not_exceed_the_target_autonomy():
    assert all(n in NAMES for _, cols in ablations.VARIANTS.values() for n in cols) and all(cols for _, cols in ablations.VARIANTS.values())
    trajs = [traj(str(i), [i / 10], [1], [0]) for i in range(10)]
    lam = ablations.match_lam(trajs, {"hi.Q1.T1": 0.5})["hi.Q1.T1"]
    assert sum(t.scores[0] <= lam for t in trajs) == 5


def test_latency_percentiles_cover_each_stage_and_the_decision_core():
    rows = [{"latency_ms": {"c3": float(i), "c4": 1.0, "c5": 2.0, "decide": 0.5, "audit_ack": 3.0}} for i in range(1, 101)]
    out = latency(rows)
    assert out["core_p50_ms"] == out["core_p99_ms"] == 3.5 and out["c3_p99_ms"] == pytest.approx(99.01) and out["audit_ack_p50_ms"] == 3.0
    assert latency([]) == {}
