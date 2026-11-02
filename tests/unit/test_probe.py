"""C8: verbalizer, probe choice, answer grammar, planner and its open contexts, response-model fit."""

import ast
from pathlib import Path

import pytest
from tests.helpers import CATALOG, EVIDENCE, OK_TEXT, decide_input, make_sp, registry, turn

from lexi.common.clock import ManualClock
from lexi.common.types import ProbeSpec, Receipt
from lexi.gate.decide import decide
from lexi.probe import fit_models
from lexi.probe.answers import parse
from lexi.probe.menu import Probe, eligible
from lexi.probe.plan import Planner
from lexi.probe.select import choose_probe, residual_risk
from lexi.probe.verbalizer import amount_phrase, digit_echo, is_confusable, spoken_amount, spoken_date
from lexi.risk.probe_update import ProbeParams

P = ProbeParams()


def _ans(text: str, kind: str, slot: str, options: list[str] | None = None, **kw):
    spec = ProbeSpec(probe_id="p", kind=kind, slot=slot, cell="hi.Q2.T1", stage_index=1, options=options or [])  # type: ignore[arg-type]
    return parse(EVIDENCE.build(turn(text, mode="PROBE_ANSWER", **kw)), spec)


# ---- verbalizer
def test_amounts_are_spoken_as_words() -> None:
    assert spoken_amount(2500, "hi") == "do hazaar paanch sau"
    assert spoken_amount(150000, "hi") == "ek lakh pachas hazaar"
    assert spoken_amount(35000, "ta") == "muppadhu anju aayiram"
    assert spoken_amount(100, "ta") == "nooru" and spoken_amount(300, "ta") == "munnooru"
    assert spoken_date("2026-10-12", "hi") == "barah october"


def test_confusable_amounts_get_a_digit_echo() -> None:
    assert amount_phrase(35000, "hi") == ("paintees hazaar - teen, paanch, hazaar", True)  # the spec's own example
    assert amount_phrase(2500, "hi") == ("do hazaar paanch sau", False)
    assert all(is_confusable(a) for a in (13000, 14000, 15000, 17000, 45000, 1700000))
    assert not is_confusable(3500) and digit_echo(17500, "hi") == "ek, saat, hazaar, paanch, sau"


# ---- choice of probe
def test_forced_choice_beats_yes_no_when_acquiescence_is_high() -> None:
    sp = make_sp("sharma ji ka paintees sau jama kar do", conf=0.97)
    both = [Probe("CONTRAST", "party", ("P01", "P02")), Probe("READBACK", "action")]
    assert choose_probe(sp, both, ProbeParams(kappa=0.0)).kind == "READBACK"  # nobody says yes to a wrong read-back: confirming is cheaper
    assert choose_probe(sp, both, ProbeParams(kappa=0.6)).kind == "CONTRAST"  # A3: yes/no loses once callers acquiesce
    assert residual_risk(sp, both[1], ProbeParams(kappa=0.4)) > residual_risk(sp, both[1], ProbeParams(kappa=0.0))
    dup = [Probe("CONTRAST", "dup", ("new", "same")), Probe("READBACK", "dup")]
    assert choose_probe(sp, dup, ProbeParams()).kind == "CONTRAST"  # 4.5: the duplicate guard is a forced choice
    assert all(residual_risk(sp, p, ProbeParams()) < 1 - sp.slots["party"].p_top * sp.slots["amount"].p_top for p in both)


def test_menu_follows_the_eligibility_rules() -> None:
    sp = make_sp(OK_TEXT, conf=0.97)
    kinds = [p.kind for p in eligible(sp, "ABOVE_LAMBDA")]
    assert kinds[0] == "CONTRAST" and "REASK" in kinds
    assert [p.kind for p in eligible(sp, "DUP")] == ["CONTRAST", "READBACK"]
    signed = sp.model_copy(update={"ap": sp.ap.model_copy(update={"intent_candidates": ["CREDIT_ENTRY", "DEBIT_ENTRY"]})})
    assert [p.kind for p in eligible(signed, "INTENT")] == ["INTENT_CONTRAST"]
    assert [p.kind for p in eligible(sp, "ABOVE_LAMBDA", yes_no_only=True)] == ["READBACK"]  # ablation A3


def test_probe_package_never_names_the_threshold() -> None:
    for f in (Path(__file__).parents[2] / "src" / "lexi" / "probe").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            name = getattr(node, "id", None) or getattr(node, "arg", None) or getattr(node, "name", None) or ""
            assert name != "lam" and not name.startswith("lambda_"), f"{f.name}: {name}"


# ---- answer grammar
@pytest.mark.parametrize(("text", "idx"), [("pehla", 0), ("doosra", 1), ("ek", 0), ("do", 1), ("pehla wala", 0)])
def test_contrast_ordinal_and_digit_answers(text: str, idx: int) -> None:
    a = _ans(text, "CONTRAST", "amount", ["3500", "4500"])
    assert (a.kind, a.chosen_index) == ("CHOICE", idx)


def test_contrast_restated_value_maps_to_an_option_or_is_a_new_value() -> None:
    assert _ans("paintalis sau", "CONTRAST", "amount", ["3500", "4500"]).chosen_index == 1
    a = _ans("paanch hazaar", "CONTRAST", "amount", ["3500", "4500"])
    assert a.kind == "VALUE" and a.value_entity_id


def test_party_contrast_by_name() -> None:
    assert _ans("verma", "CONTRAST", "party", ["P01", "P02"]).chosen_index == 1


def test_yes_no_and_corrections() -> None:
    assert _ans("haan sahi hai", "READBACK", "action").kind == "YES"
    assert _ans("nahi", "READBACK", "action").kind == "NO"
    a = _ans("nahi pachas hazaar", "READBACK", "action")
    assert a.kind == "NO_WITH_VALUE" and a.value_entity_id
    assert _ans("haan nahi", "READBACK", "action").kind == "UNCLEAR"


def test_intent_dup_reask_and_silence() -> None:
    assert _ans("jama", "INTENT_CONTRAST", "intent", ["CREDIT_ENTRY", "DEBIT_ENTRY"]).chosen_index == 0
    assert _ans("udhaar", "INTENT_CONTRAST", "intent", ["CREDIT_ENTRY", "DEBIT_ENTRY"]).chosen_index == 1
    assert _ans("naya", "CONTRAST", "dup", ["new", "same"]).chosen_index == 0
    assert _ans("wahi", "CONTRAST", "dup", ["new", "same"]).chosen_index == 1
    assert _ans("paanch sau", "REASK", "amount").kind == "VALUE"
    assert _ans("", "CONTRAST", "amount", ["1", "2"], status="SILENCE").kind == "SILENCE"
    assert _ans("kuch bhi", "CONTRAST", "amount", ["3500", "4500"]).kind == "UNCLEAR"
    assert _ans("sharma ji ka paanch sau jama karo", "CONTRAST", "amount", ["3500", "4500"]).kind == "CONTINUE"
    assert _ans("hmm", "REASK", "amount", status="TIMEOUT").kind == "UNCLEAR"


def test_tamil_answers() -> None:
    a = _ans("முதல்", "CONTRAST", "amount", ["3500", "4500"], lang="ta")
    assert (a.kind, a.chosen_index) == ("CHOICE", 0)
    assert _ans("ஆமா", "READBACK", "action", lang="ta").kind == "YES"


# ---- planner
def _probe_decision(text: str = OK_TEXT, conf: float = 0.97, lam: float = 0.001):
    sp = make_sp(text, conf=conf)
    return sp, decide(decide_input(sp, reg=registry(lam=lam, lease=1e12)), CATALOG)


def test_planner_asks_a_probe_and_hears_the_answer() -> None:
    clock = ManualClock(100.0)
    pl = Planner(CATALOG, P, clock)
    sp, d = _probe_decision()
    assert d.kind == "PROBE"
    plan = pl.plan("s1", d, sp)
    spec = plan.expects.probe_spec
    assert plan.expects.kind == "PROBE_ANSWER" and spec and spec.cell == d.cell and spec.stage_index == 1 and plan.segments[0].lang == "hi"
    assert spec.kind == "CONTRAST" and "Pehla" in plan.segments[0].text
    clock.advance(2.0)
    eb = EVIDENCE.build(turn("pehla", mode="PROBE_ANSWER"))
    out = pl.on_answer(eb)
    assert out is not None and out.kind == "CHOICE" and out.base_sp.sp_id == sp.sp_id and out.latency_ms >= 0 and out.spec == spec
    assert pl.on_answer(eb) is None  # the context is consumed


def test_digit_echo_goes_into_the_spoken_probe() -> None:
    sp, d = _probe_decision()
    amount = sp.slots["amount"].model_copy(update={"top": 35000, "runner_up": 45000, "p_top": 0.6, "p_second": 0.35})
    sp35 = sp.model_copy(update={"slots": {**sp.slots, "amount": amount}})
    d35 = d.model_copy(update={"probe_request": d.probe_request.model_copy(update={"base_sp": sp35})})
    plan = Planner(CATALOG, P, ManualClock()).plan("s1", d35, sp35)
    text = plan.segments[0].text
    assert "paintees hazaar - teen, paanch, hazaar" in text and "paintalis hazaar - chaar, paanch, hazaar" in text and plan.segments[0].prosody_hint == "digitwise"


def test_terminal_decisions_and_contexts() -> None:
    clock = ManualClock(0.0)
    pl = Planner(CATALOG, P, clock)
    sp, d = _probe_decision()
    pl.plan("s1", d, sp)
    esc = decide(decide_input(sp, reg=None), CATALOG)
    plan = pl.plan("s1", esc, sp)
    assert plan.expects.kind == "NONE" and not plan.barge_in_allowed and pl.on_answer(EVIDENCE.build(turn("pehla", mode="PROBE_ANSWER"))) is None
    pl.plan("s1", d, sp)
    clock.advance(200)
    pl.expire(clock.now())
    assert pl.on_answer(EVIDENCE.build(turn("pehla", mode="PROBE_ANSWER"))) is None
    pl.plan("s1", d, sp)
    pl.close_session("s1")
    assert pl.on_answer(EVIDENCE.build(turn("pehla", mode="PROBE_ANSWER"))) is None


def test_reprompt_refuse_and_tamil() -> None:
    pl = Planner(CATALOG, P, ManualClock())
    miss = make_sp("sharma ji ka jama kar do")
    d = decide(decide_input(miss, reg=None), CATALOG)
    assert d.kind == "REPROMPT" and pl.plan("s1", d, miss).expects.kind == "NEW_COMMAND"
    over = make_sp("sharma ji ka do lakh jama kar do", conf=0.99)
    assert "badi rakam" in pl.plan("s1", decide(decide_input(over, reg=None), CATALOG), over).segments[0].text
    ta = make_sp("ஷர்மா முப்பது ஐந்து ஆயிரம் வரவு", lang="ta")
    plan = pl.plan("s1", decide(decide_input(ta, reg=registry(lam=0.001, lease=1e12)), CATALOG), ta)
    assert plan.segments[0].lang == "ta"


def _receipt(status: str, **kw) -> Receipt:
    base = dict(entry_id="e1", status=status, intent="CREDIT_ENTRY", party_display="Sharma Kirana", amount_inr=35000, date=None,
                balance_after_tentative=0, session_id="s1", lang="hi")
    return Receipt(**{**base, **kw})  # type: ignore[arg-type]


def test_receipt_plans_and_undo_window() -> None:
    pl = Planner(CATALOG, P, ManualClock())
    plan = pl.plan_receipt(_receipt("PENDING"))
    assert plan.expects.kind == "UNDO_WINDOW" and plan.expects.receipt_id == "e1" and plan.expects.facts["amount"] == 35000
    assert "jama kar diya" in plan.segments[0].text and "'galat' boliye" in plan.segments[0].text and "teen, paanch, hazaar" in plan.segments[0].text
    assert pl.on_answer(EVIDENCE.build(turn("theek hai", mode="UNDO_WINDOW"))) is None  # CONTINUE: a new command
    undo = pl.on_answer(EVIDENCE.build(turn("galat", mode="UNDO_WINDOW")))
    assert undo is not None and undo.entry_id == "e1" and undo.session_id == "s1"  # type: ignore[union-attr]
    assert pl.on_answer(EVIDENCE.build(turn("galat", mode="UNDO_WINDOW"))) is None  # one undo per receipt
    assert pl.plan_receipt(_receipt("UNDONE")).segments[0].text.startswith("Theek hai")
    assert pl.plan_receipt(_receipt("POSTED")).segments == []
    assert pl.plan_receipt(_receipt("REJECTED")).expects.kind == "NONE"
    promise = pl.plan_receipt(_receipt("PENDING", intent="PROMISE_TO_PAY", date="2026-10-20", amount_inr=2000))
    assert "vaada" in promise.segments[0].text and "bees october" in promise.segments[0].text


def test_ta_receipt_uses_tamil_templates() -> None:
    assert "kanakkula" in Planner(CATALOG, P, ManualClock()).plan_receipt(_receipt("PENDING", lang="ta")).segments[0].text


# ---- response-model fit
def test_fit_models_recovers_rates_and_is_versioned() -> None:
    rows = ([{"probe_kind": "CONTRAST", "slot": "amount", "answer_kind": "CHOICE", "answer_correct": i % 10 != 0, "options_has_gold": True} for i in range(2000)]
            + [{"probe_kind": "READBACK", "slot": "action", "answer_kind": "YES" if i % 2 else "NO", "answer_correct": False, "options_has_gold": False}
               for i in range(2000)]
            + [{"probe_kind": "REASK", "slot": "amount", "answer_kind": "UNCLEAR" if i % 5 == 0 else "VALUE", "answer_correct": True, "options_has_gold": True}
               for i in range(2000)])
    m = fit_models.fit(rows)
    assert abs(m["eps_c"] - 0.10) < 0.01 and abs(m["kappa"] - 0.50) < 0.01 and abs(m["p_unclear"]["REASK"] - 0.20) < 0.02
    assert m["version"] == fit_models.fit(rows)["version"] and ProbeParams(**m).kappa == m["kappa"]
    assert fit_models.fit([])["eps_c"] == ProbeParams().eps_c  # no data: the documented defaults
