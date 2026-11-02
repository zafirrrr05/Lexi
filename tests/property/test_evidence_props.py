from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import EVIDENCE, turn


@settings(max_examples=60, deadline=None)
@given(st.integers(min_value=1, max_value=99_99_999))
def test_digit_amounts_round_trip(n):
    assert EVIDENCE.build(turn(f"verma ko {n} jama", conf=0.99)).lattice.of_type("AMOUNT")[0].candidates[0].value == n


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from("sharma ji ka paanch sau hazaar do nahi matlab kal jama aaya verma 250 xyzzy".split()), max_size=14),
       st.sampled_from([0.2, 0.9, None]))
def test_any_transcript_is_deterministic_and_well_formed(words, conf):
    t = turn(" ".join(words), conf=conf, status="OK" if words else "EMPTY")
    a, b = EVIDENCE.build(t), EVIDENCE.build(t)
    assert a.digest == b.digest
    ids = [e.id for e in a.lattice.entities]
    assert ids == [f"E{i + 1}" for i in range(len(ids))]
    for e in a.lattice.entities:
        assert 0 <= e.span[0] < e.span[1] <= len(words)
        assert not e.candidates or abs(sum(c.p for c in e.candidates) - 1) < 1e-9
