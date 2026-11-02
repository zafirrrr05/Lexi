import asyncio
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import EVIDENCE, turn

from lexi.common.catalog import load_catalog
from lexi.common.types import EvonRequest, EvonResponse
from lexi.parser.evon_parse import annotated_text, build_prompt, load_template, validate
from lexi.parser.parser import Parser

CAT = load_catalog()


def eb_of(text: str, **kw):
    return EVIDENCE.build(turn(text, conf=kw.pop("conf", 0.99), **kw))


def evon_says(payload, status="OK"):
    calls = []

    async def call(req: EvonRequest) -> EvonResponse:
        calls.append(req)
        return EvonResponse(status=status, text=payload if isinstance(payload, str) else json.dumps(payload), latency_ms=300.0, model_version="evon-test")
    call.calls = calls  # type: ignore[attr-defined]
    return call


def run(parser: Parser, eb):
    return asyncio.run(parser.parse(eb))


@pytest.mark.parametrize("text,intent,slots", [
    ("sharma ji ka paintees hazaar jama kar do", "CREDIT_ENTRY", {"party", "amount"}),
    ("verma ka dedh hazaar udhaar likh do", "DEBIT_ENTRY", {"party", "amount"}),
    ("gupta ji paanch hazaar agle hafte denge", "PROMISE_TO_PAY", {"party", "amount", "date"}),
    ("sharma ka due date badal do kal", "RESCHEDULE_DUE", {"party", "date"})])
def test_tier1_unambiguous_needs_no_evon(text, intent, slots):
    evon = evon_says({})
    ap = run(Parser(CAT, evon), eb_of(text))
    assert ap.intent == intent and set(ap.slots) == slots and ap.parse_source == "DET" and not evon.calls and ap.fail is None
    assert ap.intent_agreement and ap.missing_slots == []


def test_missing_slot_is_reported_not_guessed():
    ap = run(Parser(CAT, None), eb_of("sharma ji ka jama kar do"))
    assert ap.intent == "CREDIT_ENTRY" and ap.missing_slots == ["amount"] and ap.fail is None


def test_conflicting_strong_cues_call_evon_and_the_sign_guard_trips_on_disagreement():
    eb = eb_of("sharma ka paanch hazaar jama udhaar")
    assert run(Parser(CAT, None), eb).fail == "parse_ambiguous"
    ids = {e.type: e.id for e in eb.lattice.entities}
    ap = run(Parser(CAT, evon_says({"intent": "CREDIT_ENTRY", "slots": {"party": ids["PARTY"], "amount": ids["AMOUNT"]}})), eb)
    assert ap.intent == "CREDIT_ENTRY" and ap.parse_source == "DET+EVON" and ap.evon.model_version == "evon-test"
    assert not ap.intent_agreement and ap.intent_candidates == ["CREDIT_ENTRY", "DEBIT_ENTRY"]  # det saw two strong cues


def test_no_cue_words_goes_to_evon_and_agreement_needs_a_det_cue():
    eb = eb_of("sharma ji paintees hazaar")
    ids = {e.type: e.id for e in eb.lattice.entities}
    ap = run(Parser(CAT, evon_says({"intent": "DEBIT_ENTRY", "slots": {"party": ids["PARTY"], "amount": ids["AMOUNT"]}})), eb)
    assert ap.parse_source == "EVON" and ap.intent == "DEBIT_ENTRY" and not ap.intent_agreement
    assert ap.slots == {"party": ids["PARTY"], "amount": ids["AMOUNT"]}


@pytest.mark.parametrize("payload", ["not json", {"intent": "HACK", "slots": {}}, {"intent": "CREDIT_ENTRY", "slots": {"amount": "E99"}},
                                     {"intent": "CREDIT_ENTRY", "slots": {"amount": "E1"}}, {"intent": "CREDIT_ENTRY", "slots": {"x": "E1"}}])
def test_invalid_evon_answers_fail_closed(payload):
    ap = run(Parser(CAT, evon_says(payload)), eb_of("sharma ji paintees hazaar"))
    assert ap.fail == "evon_invalid" and ap.intent is None and ap.slots == {}


def test_evon_timeout_and_disabled_evon():
    eb = eb_of("sharma ji paintees hazaar")
    assert run(Parser(CAT, evon_says("", status="TIMEOUT")), eb).fail == "evon_timeout"
    assert run(Parser(CAT, None), eb).fail == "no_intent"
    assert run(Parser(CAT, None), eb_of("sharma jama udhaar")).fail == "parse_ambiguous"


def test_asr_and_confidence_failures_still_produce_an_ap():
    assert run(Parser(CAT, None), eb_of("", status="EMPTY")).fail == "asr_failed"
    assert run(Parser(CAT, None), eb_of("sharma ka paanch sau jama", conf=None)).fail == "conf_missing"


def test_prompt_format_matches_the_contract():
    eb = eb_of("sharma ji ka paintees hazaar jama kar do")
    template, schema = load_template()
    prompt = build_prompt(template, eb, CAT)
    assert "[E1:party|sharma ji] ka [E2:amount|paintees hazaar] jama kar do" in annotated_text(eb)
    assert "ALLOWED_INTENTS: CREDIT_ENTRY, DEBIT_ENTRY, PROMISE_TO_PAY, RESCHEDULE_DUE" in prompt
    assert "REQUIRED_SLOTS: CREDIT_ENTRY=party,amount; DEBIT_ENTRY=party,amount; PROMISE_TO_PAY=party,amount,date; RESCHEDULE_DUE=party,date" in prompt
    assert prompt.endswith("JSON:") and schema["required"] == ["intent", "slots"]
    assert validate('{"intent": "CREDIT_ENTRY", "slots": {"party": "E1", "amount": "E2"}}', eb, CAT) == ("CREDIT_ENTRY", {"party": "E1", "amount": "E2"})


@settings(max_examples=80, deadline=None)
@given(st.lists(st.sampled_from("sharma ji ka paanch sau hazaar do jama udhaar denge kal agle hafte verma xyzzy 250 nahi nahi".split()), min_size=1, max_size=12))
def test_ap_slots_always_resolve_to_lattice_entities_of_the_right_type(words):
    """I2: whatever Evon says (here: adversarial ids), every slot value is an entity of the EB and of the slot's type."""
    eb = eb_of(" ".join(words))
    wild = evon_says({"intent": "PROMISE_TO_PAY", "slots": {"party": "E1", "amount": "E2", "date": "E3"}})
    for parser in (Parser(CAT, None), Parser(CAT, wild)):
        ap = run(parser, eb)
        for slot, eid in ap.slots.items():
            ent = eb.lattice.get(eid)
            assert ent is not None and ent.type == {"party": "PARTY", "amount": "AMOUNT", "date": "DATE"}[slot]
