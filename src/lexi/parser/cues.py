"""Intent cues (C4 step 1): cue classes found in the lattice, mapped to catalog intents."""

from lexi.common.types import EvidenceBundle, Intent

INTENT_OF = {"CREDIT": "CREDIT_ENTRY", "DEBIT": "DEBIT_ENTRY", "PROMISE": "PROMISE_TO_PAY", "RESCHEDULE": "RESCHEDULE_DUE"}
STRONG = 0.8


def intent_cues(eb: EvidenceBundle) -> dict[Intent, float]:
    """Strongest cue strength per intent."""
    out: dict[Intent, float] = {}
    for e in eb.lattice.of_type("CUE"):
        assert e.cue is not None
        if e.cue.cls in INTENT_OF:
            intent: Intent = INTENT_OF[e.cue.cls]  # type: ignore[assignment]
            out[intent] = max(out.get(intent, 0.0), e.cue.strength)
    return out
