"""Join by call_id (C14 step 2): COLLECT trajectories become CalibrationRecords, LIVE commits become LabeledOutcomes."""

from collections.abc import Mapping
from typing import Any

from lexi.common.types import CalibrationRecord, GoldLabel, LabeledOutcome
from lexi.labelstore.records import is_wrong, tops


def trajectory(gold: GoldLabel, payloads: list[Mapping[str, Any]], ts: float) -> CalibrationRecord | None:
    """One record per COLLECT call: the last scored decision of every stage, in stage order."""
    by_stage: dict[int, Mapping[str, Any]] = {}
    for p in payloads:
        if p["collect"] is not None:
            by_stage[p["stage_index"]] = p  # a re-scored stage (after a reprompt) replaces the earlier attempt
    if not by_stage:
        return None
    stages = [by_stage[j] for j in sorted(by_stage)]
    if stages[0]["cell"] is None:  # no action was recognised at stage 0, so no cell: such a call can never be accepted and is outside every certificate
        return None
    return CalibrationRecord(
        call_id=gold.call_id, cell=stages[0]["cell"], scores=[p["sp"]["s"] for p in stages], elig=[p["collect"]["el"] for p in stages],
        labels=[1 if p["sp"]["flags"]["degraded"] else is_wrong(tops(p["sp"]), gold) for p in stages],
        feature_vectors=[p["sp"]["feature_vector"] for p in stages], scorer_version=stages[-1]["sp"]["scorer_version"],
        split=gold.split, scenario=gold.scenario, tags=gold.tags, ts=ts)


def live_commit(gold: GoldLabel, payloads: list[Mapping[str, Any]], ts: float) -> LabeledOutcome | None:
    commit = next((p for p in payloads if p["mode"] == "LIVE" and p["decision"]["kind"] == "COMMIT"), None)
    if commit is None:
        return None
    values = tops(commit["sp"])
    return LabeledOutcome(
        call_id=gold.call_id, cell=commit["cell"], decision_kind="COMMIT", committed_values=values, y_final=is_wrong(values, gold),
        stage_index=commit["stage_index"], score_trajectory=[p["sp"]["s"] for p in payloads if p["mode"] == "LIVE"],
        label_tier="G", autonomous=True, ts=ts)


def human(gold: GoldLabel, call_id: str, values: Mapping[str, Any], cell: str | None, ts: float) -> LabeledOutcome:
    committed = {"intent": gold.gold_intent} | dict(values)  # the operator picks slot values; the action itself is not in question
    return LabeledOutcome(call_id=call_id, cell=cell, decision_kind="COMMIT", committed_values=committed, y_final=is_wrong(committed, gold),
                          stage_index=0, score_trajectory=[], label_tier="A", autonomous=False, ts=ts)


def summary(gold: GoldLabel, payloads: list[Mapping[str, Any]], outcome: LabeledOutcome | None) -> dict[str, Any] | None:
    """One finished LIVE call for the console's counters: what LEXI did, what the naive baseline would have posted at stage 0, and who was right."""
    live = [p for p in payloads if p["mode"] == "LIVE"]
    if not live:
        return None
    first = min(live, key=lambda p: p["stage_index"])
    base = None
    if first["decision"]["baseline_shadow"]["kind"] == "COMMIT":
        values = tops(first["sp"])
        base = {"values": values, "wrong": is_wrong(values, gold)}
    commit = next((p for p in live if p["decision"]["kind"] == "COMMIT"), None)
    view = (first["turn_input"] or {}).get("ledger_view") or {}
    return {"call_id": gold.call_id, "account": view.get("account"), "names": {x["id"]: x["display"] for x in view.get("parties", [])}, "scenario": gold.scenario, "cell": first["cell"], "final": "COMMIT" if commit else "ESCALATE",
            "certified": bool(commit and commit["decision"]["cert_ref"] and not commit["decision"]["cert_ref"]["hedged"]),
            "values": outcome.committed_values if outcome else None, "wrong": outcome.y_final if outcome else None, "baseline": base,
            "probes": len({p["stage_index"] for p in live}) - 1, "ts": first["sp"]["eb"]["clock_ts"]}
