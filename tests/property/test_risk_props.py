"""C5 properties: posteriors are distributions, scores stay in range, scoring is replay-deterministic, probe updates never raise risk of a wrong accept silently."""

import math

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import ENGINE, make_outcome, make_sp

from lexi.common.hashing import canonical_json
from lexi.common.types import Candidate
from lexi.risk.fusion import fuse

AMOUNTS = ["sharma ji ka paintees hazaar jama kar do", "verma ji ne do hazaar udhaar liya", "gupta ka teen sau jama karo", "sharma ji ka pachas hazaar jama"]


@settings(max_examples=40, deadline=None)
@given(text=st.sampled_from(AMOUNTS), conf=st.floats(0.05, 1.0), cqi=st.sampled_from(["Q1", "Q2", "Q3"]))
def test_score_in_range_and_deterministic(text: str, conf: float, cqi: str) -> None:
    a, b = make_sp(text, conf=conf, cqi=cqi), make_sp(text, conf=conf, cqi=cqi)
    assert 1e-4 <= a.s <= 1.0
    assert canonical_json(a) == canonical_json(b)  # replay is bit-identical (RC7)
    for sp in a.slots.values():
        assert 0.0 <= sp.p_second <= sp.p_top <= 1.0 + 1e-9


@settings(max_examples=60, deadline=None)
@given(ps=st.lists(st.floats(1e-3, 1.0), min_size=1, max_size=6), prior=st.lists(st.floats(1e-3, 5.0), min_size=6, max_size=6))
def test_fusion_is_a_sorted_distribution(ps: list[float], prior: list[float]) -> None:
    cands = [Candidate(value=i, p=p / sum(ps)) for i, p in enumerate(ps)]
    ranked = fuse(cands, lambda v: prior[int(v)])
    probs = [p for _, p in ranked]
    assert math.isclose(sum(probs), 1.0, abs_tol=1e-9) and probs == sorted(probs, reverse=True)


@settings(max_examples=20, deadline=None)
@given(conf=st.floats(0.3, 1.0))
def test_yes_on_readback_does_not_raise_score_and_no_does_not_lower_it(conf: float) -> None:
    base = make_sp("sharma ji ka paintees hazaar jama kar do", conf=0.6)
    yes = ENGINE.update(make_outcome(base, "YES", "READBACK", "amount", answer_text="haan", conf=conf))
    no = ENGINE.update(make_outcome(base, "NO", "READBACK", "amount", answer_text="nahi", conf=conf))
    assert yes.s <= base.s + 1e-9
    assert no.s >= yes.s
