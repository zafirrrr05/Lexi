"""Sign guard (C4 step 3): credit and debit have opposite signs, so the intent counts as agreed only when the
deterministic classifier (one strong cue class) and, if it was called, Evon name the same intent."""

from lexi.common.types import Intent

SIGNED: list[Intent] = ["CREDIT_ENTRY", "DEBIT_ENTRY"]


def guard(intent: Intent, det_strong: bool, evon_intent: Intent | None, evon_called: bool) -> tuple[bool, list[Intent]]:
    """(intent_agreement, intent_candidates). Disagreement on a signed intent offers both with equal probability downstream."""
    agreed = det_strong and (not evon_called or evon_intent == intent)
    if intent in SIGNED and not agreed:
        return False, list(SIGNED)
    return agreed, [intent]
