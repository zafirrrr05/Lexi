"""C11 verbalizer round trip (spec C11 step 2): whatever the Twin says, C3 reads back with the gold among its top-5 candidates."""

import random
from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import EVIDENCE, LIMITS, VIEW, turn

from lexi.common.types import LedgerView, PartyView
from lexi.evidence.ipk import ipk
from lexi.twin.scripts import CARRIERS, make_accounts, make_script
from lexi.twin.verbalize import verbalize_amount, verbalize_date

TODAY = date(2026, 10, 9)
ACCOUNTS = make_accounts(7, ["hi", "ta"])


def top5(eb, etype):  # noqa: ANN001, ANN201
    return [[c.value for c in e.candidates[:5]] for e in eb.lattice.entities if e.type == etype]


@settings(max_examples=250, deadline=None)
@given(st.integers(100, 25_000), st.sampled_from(["hi", "ta"]), st.integers(0, 10**6))
def test_amount_round_trip(n: int, lang: str, seed: int) -> None:
    text = " ".join(t.w for t in verbalize_amount(n, lang, random.Random(seed)))
    assert any(n in c for c in top5(EVIDENCE.build(turn(text, lang=lang)), "AMOUNT")), text


@settings(max_examples=250, deadline=None)
@given(st.integers(0, 60), st.sampled_from(["hi", "ta"]), st.integers(0, 10**6))
def test_date_round_trip(offset: int, lang: str, seed: int) -> None:
    d = TODAY + timedelta(days=offset)
    cue = "vaada" if lang == "hi" else "tharuvaanga"  # a promise cue makes "<day> ko" a date
    text = cue + " " + " ".join(t.w for t in verbalize_date(d, TODAY, lang, random.Random(seed)))
    assert any(d.isoformat() in c for c in top5(EVIDENCE.build(turn(text, lang=lang, view=VIEW.model_copy(update={"today": TODAY.isoformat()}))), "DATE")), text


def test_scripts_round_trip_with_gold_in_top5() -> None:
    rng = random.Random(11)
    for lang in ("hi", "ta"):
        for stratum in ("plain", "rtp", "minimal_pair"):
            for _ in range(60):
                s = make_script(rng, ACCOUNTS, lang, stratum, TODAY)
                acct = next(a for a in ACCOUNTS if a.account == s.account)
                view = LedgerView(account=acct.account, today=TODAY.isoformat(), limits=LIMITS, parties=[
                    PartyView(id=p.id, display=p.display, aliases=list(p.aliases), ipk=ipk(p.display, lang), balance=p.balance,
                              amount_hist=list(p.amount_hist), last_entry_ts=p.last_entry_ts) for p in acct.parties])
                text = " ".join(t.w for t in s.tokens)
                eb = EVIDENCE.build(turn(text, lang=lang, view=view))
                for slot, etype in (("party", "PARTY"), ("amount", "AMOUNT"), ("date", "DATE")):
                    if slot in s.gold:
                        assert any(s.gold[slot] in c for c in top5(eb, etype)), (text, slot, s.gold)


def test_every_intent_and_language_has_at_least_twenty_carrier_phrases() -> None:
    assert {k: len(v) for k, v in CARRIERS.items() if len(v) < 20} == {}
    assert len(CARRIERS) == 8


def test_script_id_is_stable_and_the_gold_matches_the_script() -> None:
    a = make_script(random.Random(5), ACCOUNTS, "hi", "rtp", TODAY)
    b = make_script(random.Random(5), ACCOUNTS, "hi", "rtp", TODAY)
    assert a.script_id == b.script_id and a.gold == b.gold
    assert a.gold["intent"] == a.intent and a.gold["party"] == a.party_id
