import time

import pytest
from tests.helpers import EVIDENCE, turn

from lexi.evidence.builder import Evidence
from lexi.evidence.fit_confusion import fit


def ents(text: str, etype: str, **kw):
    return EVIDENCE.build(turn(text, **kw)).lattice.of_type(etype)


def top(text: str, etype: str, **kw):
    return ents(text, etype, **kw)[0].candidates[0].value


@pytest.mark.parametrize("text,value", [
    ("paintees hazaar", 35000), ("ek lakh pachas hazaar", 150000), ("dedh sau", 150), ("saade teen hazaar", 3500),
    ("paune do lakh", 175000), ("sawa lakh", 125000), ("dhai hazaar", 2500), ("paanch sau hazaar", 500000),
    ("सत्रह सौ", 1700), ("पैंतीस हज़ार", 35000), ("2500", 2500), ("₹2500", 2500), ("do crore", 20000000),
    ("ek hazaar aur paanch sau", 1500)])
def test_amount_grammar(text, value):
    assert top(text, "AMOUNT", conf=0.99) == value


def test_scale_grouping_is_ambiguous():
    cands = {c.value: c.p for c in ents("do sau paanch hazaar", "AMOUNT", conf=0.99)[0].candidates}
    assert cands[205000] > cands[5200] > 0


def test_confusable_pair_keeps_both_readings():
    cands = {c.value: c.p for c in ents("satrah hazaar", "AMOUNT", conf=0.7)[0].candidates}
    assert cands[17000] > cands[70000] > 0.02


def test_candidates_are_normalised_and_at_most_k():
    for e in EVIDENCE.build(turn("sharma ji ka paintees hazaar jama kar do")).lattice.entities:
        if e.candidates:
            assert abs(sum(c.p for c in e.candidates) - 1) < 1e-9 and len(e.candidates) <= 5


def test_verb_do_is_not_the_numeral_two():
    assert not ents("sharma ji ka paintees hazaar jama kar do", "AMOUNT")[0].span[1] > 5


def test_party_match_and_out_of_registry():
    assert top("sharma ji ka paanch sau jama", "PARTY") == "P01"
    assert top("ramesh ka paanch sau jama", "PARTY") == "OUT_OF_REGISTRY"


def test_party_carries_ledger_context():
    eb = EVIDENCE.build(turn("verma ko paanch sau udhaar"))
    assert eb.prior_context["P02"].display == "Verma Traders" and "OUT_OF_REGISTRY" not in eb.prior_context


@pytest.mark.parametrize("text,iso", [("kal", "2026-10-10"), ("agle hafte", "2026-10-16"), ("paanch tarikh", "2026-11-05"),
                                      ("pachees tarikh", "2026-10-25"), ("somvar", "2026-10-12"), ("das november", "2026-11-10")])
def test_dates_resolve_against_ledger_date(text, iso):
    assert top(text, "DATE", conf=0.99) == iso


def test_repair_weights_and_flag():
    eb = EVIDENCE.build(turn("sharma ka pachas hazaar nahi nahi paintees hazaar aaya", conf=0.99))
    amount = eb.lattice.of_type("AMOUNT")[0]
    by = {(c.value, c.parse_id): c.p for c in amount.candidates}
    assert eb.flags.repair_present and amount.features.repaired
    assert by[(35000, 0)] > 0.7 and abs(by[(50000, 1)] - 0.15) < 0.02


def test_cues_in_command_mode_exclude_answer_words():
    cls = {e.cue.cls for e in EVIDENCE.build(turn("haan sharma jama")).lattice.of_type("CUE")}
    assert "CREDIT" in cls and "YES" not in cls
    cls = {e.cue.cls for e in EVIDENCE.build(turn("haan", mode="PROBE_ANSWER")).lattice.of_type("CUE")}
    assert "YES" in cls


def test_truncation_flag_needs_numeric_tail_and_short_silence():
    assert EVIDENCE.build(turn("sharma ka paanch sau", silence_ms=100)).flags.maybe_truncated
    assert not EVIDENCE.build(turn("sharma ka paanch sau", silence_ms=600)).flags.maybe_truncated
    assert not EVIDENCE.build(turn("sharma ka paanch sau jama", silence_ms=100)).flags.maybe_truncated


def test_failure_flags():
    assert EVIDENCE.build(turn("", status="EMPTY")).flags.asr_failed
    eb = EVIDENCE.build(turn("sharma ka paanch sau jama", conf=None))
    assert eb.flags.conf_missing and not eb.flags.asr_failed and eb.lattice.of_type("AMOUNT")


def test_tamil_amount_and_cue():
    eb = EVIDENCE.build(turn("முப்பது ஐந்து ஆயிரம் வரவு", lang="ta", conf=0.99))
    assert eb.lattice.of_type("AMOUNT")[0].candidates[0].value == 35000


def test_deterministic_and_fast():
    t = turn("sharma ji ka paintees hazaar jama kar do agle hafte " * 3)
    a = EVIDENCE.build(t)
    t0 = time.perf_counter()
    for _ in range(20):
        b = EVIDENCE.build(t)
    assert a.digest == b.digest and a.eb_id == b.eb_id
    assert (time.perf_counter() - t0) / 20 < 0.015


def test_fit_learns_a_confusion_and_repair_weight():
    recs = [{"type": "token", "lang": "hi", "cqi": "Q3", "bandwidth_class": "NB", "spoken": ["satrah", "hazaar"],
             "heard": [{"w": "sattar", "conf": 0.5}, {"w": "hazaar", "conf": 0.9}]} for _ in range(10)]
    recs += [{"type": "repair", "post_correct": True}] * 30
    tables = fit(recs)
    ev = Evidence.load()
    learned = Evidence(ev.lexicons, type(ev.confusion)(tables))
    cands = {c.value: c.p for c in learned.build(turn("sattar hazaar", cqi="Q3")).lattice.of_type("AMOUNT")[0].candidates}
    assert cands[17000] > 0.9 and tables["repair_post"] > 0.85 and learned.version != ev.version
