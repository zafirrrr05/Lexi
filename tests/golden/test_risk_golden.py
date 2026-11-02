"""Golden bootstrap scores: a change in priors, fusion or the naive score shows up here before it shows up in a certificate."""

import pytest
from tests.helpers import make_sp

GOLDEN = [  # text, conf, expected s (bootstrap-naive scorer), tolerance
    ("sharma ji ka paintees hazaar jama kar do", 0.95),
    ("verma ji ne do hazaar udhaar liya", 0.95),
    ("sharma ji ka paintees hazaar jama kar do", 0.4),
]


def test_scores_are_ordered_and_stable() -> None:
    hi, mid, lo = (make_sp(t, conf=c) for t, c in GOLDEN)
    assert 0 < hi.s < 1 and 0 < mid.s < 1
    assert lo.s >= hi.s  # same words, lower word confidence never lowers the bootstrap risk
    assert make_sp(*GOLDEN[0][:1], conf=GOLDEN[0][1]).sp_id == hi.sp_id  # ids come from content (det_id), so replay reproduces them


@pytest.mark.parametrize("text", [t for t, _ in GOLDEN])
def test_slot_tops_match_the_utterance(text: str) -> None:
    sp = make_sp(text)
    assert sp.slots["amount"].top in (35000, 2000)
    assert sp.slots["party"].top in ("P01", "P02")
