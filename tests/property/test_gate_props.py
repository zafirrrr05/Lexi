"""C7 properties (spec C7 EARNS): fuzzed over random SP, registry and flags, a COMMIT never happens when it must not."""

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import CATALOG, decide_input, make_sp, registry

from lexi.common.types import DialogueCtx, SpFlags
from lexi.gate.decide import decide

TEXTS = ["sharma ji ka do hazaar jama kar do", "sharma ji ka pachas hazaar jama kar do", "verma ji ne ek lakh udhaar liya", "gupta ka teen sau jama"]
SPS = {t: make_sp(t, conf=0.9) for t in TEXTS}


@settings(max_examples=300, deadline=None)
@given(text=st.sampled_from(TEXTS), s=st.floats(1e-4, 1.0), lam=st.one_of(st.none(), st.floats(1e-4, 0.5)), lease=st.sampled_from([5.0, 1e12]),
       degraded=st.booleans(), agree=st.booleans(), audit_ok=st.booleans(), ledger_ok=st.booleans(), probes=st.integers(0, 3), turns=st.integers(1, 9),
       entries=st.integers(0, 11), mode=st.sampled_from(["LIVE", "COLD_START", "COLLECT"]), pair_ok=st.booleans())
def test_commit_only_when_every_condition_holds(text, s, lam, lease, degraded, agree, audit_ok, ledger_ok, probes, turns, entries, mode, pair_ok) -> None:  # type: ignore[no-untyped-def]
    base = SPS[text]
    sp = base.model_copy(update={"s": s, "flags": SpFlags(degraded=degraded), "intent_agreement": agree,
                                 "ctx": DialogueCtx(mode="NEW_COMMAND", probes_used=probes, turns_used=turns, reprompts_used=0, entries_in_session=entries)})
    reg = registry(lam=lam, lease=lease, scorer="bootstrap-naive" if pair_ok else "sc-other") if lam is not None else None
    inp = decide_input(sp, reg=reg, mode=mode, audit_ok=audit_ok, ledger_ok=ledger_ok)
    d = decide(inp, CATALOG)
    amount = int(sp.slots["amount"].top)
    if d.kind == "COMMIT" and mode != "COLLECT":
        assert mode == "LIVE" and reg is not None and lam is not None  # (a) autonomy was available ...
        assert lease > inp.now and pair_ok and audit_ok and ledger_ok and not degraded
        assert s <= lam  # (b) ... the score cleared the threshold ...
        assert amount <= CATALOG.limits.max_autonomous_amount_inr and entries < CATALOG.limits.max_entries_per_session  # (c) ... limits held ...
        assert agree  # (d) ... and the intent was agreed
    if mode == "COLD_START":
        assert d.kind != "COMMIT"
    if amount > CATALOG.limits.hard_max_inr:
        assert d.kind == "REFUSE"
