"""C7 rule table R0-R8, COLLECT eligibility and the baseline shadow, one case per rule."""

import pytest
from tests.helpers import CATALOG, CELL, OK_TEXT, decide_input, make_sp, registry

from lexi.common.types import DialogueCtx, PendingEntry, SpFlags
from lexi.gate.decide import cell_of, decide, eligibility


def _go(sp, reg="default", **kw):
    reg = registry() if reg == "default" else reg
    return decide(decide_input(sp, reg=reg, **kw), CATALOG)


def _with(sp, **ctx_kw):
    return sp.model_copy(update={"ctx": sp.ctx.model_copy(update=ctx_kw)})


def test_r0_cell_from_language_cqi_tier_and_carried_cell_wins() -> None:
    sp = make_sp(OK_TEXT, cqi="Q3")
    assert cell_of(sp, CATALOG) == "hi.Q3.T1"
    assert cell_of(_with(sp, cell="hi.Q1.T1"), CATALOG) == "hi.Q1.T1"  # a probe-answer turn never moves the cell


def test_r1_reprompt_then_escalate() -> None:
    sp = make_sp("", status="SILENCE")
    assert (d := _go(sp)).kind == "REPROMPT" and d.reason_code == "ASR_FAILED"
    assert _go(_with(sp, reprompts_used=2)).reason_code == "REPROMPTS_EXHAUSTED"
    assert _go(make_sp(OK_TEXT, conf=None)).reason_code == "CONF_MISSING"
    assert _go(make_sp("sharma ji")).reason_code in ("AP_FAIL", "MISSING_SLOT")


def test_r2_missing_slot_reprompts() -> None:
    sp = make_sp("sharma ji ka jama kar do")
    assert (d := _go(sp)).kind == "REPROMPT" and d.reason_code in ("MISSING_SLOT", "AP_FAIL")


@pytest.mark.parametrize(("text", "kind", "reason"), [
    ("sharma ji ka pachas hazaar jama kar do", "ESCALATE", "OVER_MAX_AUTONOMOUS"),
    ("sharma ji ka do lakh jama kar do", "REFUSE", "OVER_HARD_MAX"),
])
def test_r3_amount_limits(text: str, kind: str, reason: str) -> None:
    d = _go(make_sp(text, conf=0.99), registry(lam=0.9))
    assert (d.kind, d.reason_code) == (kind, reason)


def test_r3_party_out_of_registry_and_session_limit() -> None:
    sp = make_sp("ramesh bhai ka do hazaar jama kar do", conf=0.99)
    if sp.slots["party"].top == "OUT_OF_REGISTRY":
        assert _go(sp, registry(lam=0.9)).reason_code == "OUT_OF_REGISTRY"
    assert _go(_with(make_sp(OK_TEXT), entries_in_session=10), registry(lam=0.9)).reason_code == "SESSION_LIMIT"


def test_r3_duplicate_guard_forces_probe_even_with_a_loose_threshold() -> None:
    sp = make_sp(OK_TEXT)
    pend = [PendingEntry(entry_id="e1", intent="CREDIT_ENTRY", party_id="P01", amount_inr=2000, date=None, status="POSTED", ts=0.0)]
    ctx = sp.eb.prior_context["P01"].model_copy(update={"session_recent": pend})
    dup = sp.model_copy(update={"eb": sp.eb.model_copy(update={"prior_context": {**sp.eb.prior_context, "P01": ctx}})})
    d = _go(dup, registry(lam=0.9))
    assert (d.kind, d.reason_code) == ("PROBE", "DUP")
    assert _go(dup.model_copy(update={"dup_cleared": True}), registry(lam=0.9)).kind == "COMMIT"


def test_r4_disagreeing_intent_never_commits() -> None:
    sp = make_sp(OK_TEXT).model_copy(update={"intent_agreement": False})
    d = _go(sp, registry(lam=0.9))
    assert (d.kind, d.reason_code) == ("PROBE", "INTENT")
    assert _go(_with(sp, probes_used=2), registry(lam=0.9)).reason_code == "PROBES_EXHAUSTED"


def test_r5_each_missing_condition_removes_autonomy() -> None:
    sp = make_sp(OK_TEXT)
    ok = _go(sp, registry(lam=0.9))
    assert ok.kind == "COMMIT" and ok.reason_code == "CERTIFIED" and ok.cert_ref and ok.cert_ref.cert_id == "C-1" and ok.lease_expiry_seen == 100.0
    assert _go(sp, registry(lam=0.9, lease=5.0)).reason_code == "NO_AUTONOMY"  # lease lapsed at now=10
    assert _go(sp, registry(lam=0.9, scorer="sc-other")).reason_code == "NO_AUTONOMY"  # release pair mismatch (F9)
    assert _go(sp, registry(lam=0.9), audit_ok=False).reason_code == "NO_AUTONOMY"
    assert _go(sp, registry(lam=0.9), ledger_ok=False).reason_code == "NO_AUTONOMY"
    assert _go(sp.model_copy(update={"flags": SpFlags(degraded=True)}), registry(lam=0.9)).reason_code == "NO_AUTONOMY"
    assert _go(sp, registry(lam=None)).reason_code == "NO_AUTONOMY"
    assert _go(sp, None).reason_code == "NO_AUTONOMY"
    assert _go(sp, registry(lam=0.9), now=10.0 + 604801).reason_code == "NO_AUTONOMY"  # registry older than the maximum age
    assert _go(sp, registry(lam=0.9), mode="COLD_START").reason_code == "NO_AUTONOMY"


def test_r6_threshold_edge_and_hedged_reason() -> None:
    sp = make_sp(OK_TEXT)
    assert _go(sp, registry(lam=sp.s)).kind == "COMMIT"  # s <= lambda
    assert _go(sp, registry(lam=sp.s * 0.99)).kind == "PROBE"
    d = _go(sp, registry(lam=0.9, hedged=True, state="WATCH"))
    assert d.reason_code == "HEDGED" and d.cert_ref and d.cert_ref.cert_id is None and d.cert_ref.hedged


def test_r7_r8_probe_budget() -> None:
    sp = make_sp(OK_TEXT)
    tight = registry(lam=sp.s / 2)
    d = _go(sp, tight)
    assert d.kind == "PROBE" and d.reason_code == "ABOVE_LAMBDA" and d.probe_request and d.probe_request.stage_index == 1
    assert _go(_with(sp, probes_used=2), tight).reason_code == "PROBES_EXHAUSTED"
    assert _go(_with(sp, turns_used=8), tight).reason_code == "PROBES_EXHAUSTED"


def test_exception_inside_decide_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    inp = decide_input(make_sp(OK_TEXT), reg=registry(lam=0.9))
    inp.sp.slots.clear()  # a malformed SP must not commit
    inp.sp.ap.missing_slots.append("amount")
    d = decide(inp, CATALOG)
    assert d.kind != "COMMIT"
    monkeypatch.setattr("lexi.gate.decide._decide", lambda *_: 1 / 0)
    d = decide(decide_input(make_sp(OK_TEXT), reg=registry(lam=0.9)), CATALOG)
    assert (d.kind, d.reason_code) == ("ESCALATE", "GATE_ERROR")


def test_collect_mode_eligibility_and_stopping() -> None:
    sp = make_sp(OK_TEXT)
    inp = decide_input(sp, mode="COLLECT", lambda_min=sp.s)
    assert eligibility(inp, CATALOG) and (d := decide(inp, CATALOG)).kind == "COMMIT" and d.reason_code == "COLLECT_STOP"
    above = decide_input(sp, mode="COLLECT", lambda_min=sp.s / 2)
    assert decide(above, CATALOG).kind == "PROBE"
    assert decide(decide_input(_with(sp, probes_used=2), mode="COLLECT", lambda_min=sp.s / 2), CATALOG).reason_code == "PROBES_EXHAUSTED"
    boot = decide_input(sp, mode="COLLECT")  # no grid yet (D11a): probe unless the naive risk is below 0.001
    assert decide(boot, CATALOG).kind == "PROBE"
    assert decide(decide_input(sp.model_copy(update={"s": 0.0005}), mode="COLLECT"), CATALOG).reason_code == "COLLECT_STOP"
    forced = sp.model_copy(update={"intent_agreement": False})  # a force_probe block is not terminal: el = 0, probing continues
    fi = decide_input(forced, mode="COLLECT", lambda_min=0.9)
    assert not eligibility(fi, CATALOG) and decide(fi, CATALOG).reason_code == "INTENT"
    over = make_sp("sharma ji ka pachas hazaar jama kar do", conf=0.99)  # an R3 breach is terminal
    assert not eligibility(decide_input(over, mode="COLLECT"), CATALOG)
    assert decide(decide_input(over, mode="COLLECT"), CATALOG).kind == "ESCALATE"
    dead = sp.model_copy(update={"flags": SpFlags(degraded=True)})
    assert decide(decide_input(dead, mode="COLLECT"), CATALOG).reason_code == "NO_AUTONOMY"


def test_baseline_shadow_follows_min_conf_threshold() -> None:
    hi, lo = make_sp(OK_TEXT, conf=0.95), make_sp(OK_TEXT, conf=0.3)
    assert _go(hi, None).baseline_shadow.kind == "COMMIT"
    assert _go(lo, None).baseline_shadow.kind == "ESCALATE"
    assert _go(make_sp("sharma ji ka pachas hazaar jama kar do", conf=0.99), None).baseline_shadow.kind == "ESCALATE"  # hard limits bind the baseline too


def test_decision_ids_are_content_derived() -> None:
    a, b = _go(make_sp(OK_TEXT)), _go(make_sp(OK_TEXT))
    assert a.decision_id == b.decision_id and a.model_dump() == b.model_dump()


def test_cell_constant_is_the_default_cell() -> None:
    assert cell_of(make_sp(OK_TEXT), CATALOG) == CELL and isinstance(make_sp(OK_TEXT).ctx, DialogueCtx)
