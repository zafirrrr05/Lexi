"""Deterministic parts of the C7 rule table (R1-R4): read only the scored proposal and the catalog limits. No model output decides any of them (P2)."""

from dataclasses import dataclass
from typing import Literal

from lexi.common.catalog import Catalog
from lexi.common.types import Intent, ScoredProposal

OOV = "OUT_OF_REGISTRY"


@dataclass(frozen=True)
class Block:
    kind: Literal["ESCALATE", "REFUSE"]
    reason: str


def intent_of(sp: ScoredProposal) -> Intent | None:
    return sp.intent or sp.ap.intent


def asr_block(sp: ScoredProposal) -> str | None:
    """R1: reason code when the turn did not yield a usable proposal."""
    if sp.eb.asr_status != "OK" or sp.eb.flags.asr_failed:
        return "ASR_FAILED"
    if sp.eb.flags.conf_missing or sp.ap.fail == "conf_missing":
        return "CONF_MISSING"
    return "AP_FAIL" if sp.ap.fail else None


def missing_slots(sp: ScoredProposal, catalog: Catalog) -> list[str]:
    """R2. A critical slot the posterior does not cover counts as missing too, so an incomplete SP can never reach R6."""
    intent = intent_of(sp)
    absent = [s for s in catalog.critical_slots(intent) if s != "intent" and s not in sp.slots] if intent else []
    return sp.ap.missing_slots or absent


def limit_block(sp: ScoredProposal, catalog: Catalog) -> Block | None:
    """R3 hard limits (4.5), on the top candidates."""
    lim = catalog.limits
    party, amount = sp.slots.get("party"), sp.slots.get("amount")
    if party is not None and party.top == OOV:
        return Block("ESCALATE", "OUT_OF_REGISTRY")
    if amount is not None and int(amount.top) > lim.hard_max_inr:
        return Block("REFUSE", "OVER_HARD_MAX")
    if amount is not None and int(amount.top) > lim.max_autonomous_amount_inr:
        return Block("ESCALATE", "OVER_MAX_AUTONOMOUS")
    if sp.ctx is not None and sp.ctx.entries_in_session >= lim.max_entries_per_session:
        return Block("ESCALATE", "SESSION_LIMIT")
    return None


def is_duplicate(sp: ScoredProposal) -> bool:
    """R3 duplicate guard: same (action, party, amount) as an entry C9 lists for this session inside the duplicate window."""
    party, amount = sp.slots.get("party"), sp.slots.get("amount")
    ctx = sp.eb.prior_context.get(str(party.top)) if party is not None else None
    if sp.dup_cleared or ctx is None:
        return False
    want = int(amount.top) if amount is not None else None
    return any(e.intent == intent_of(sp) and e.amount_inr == want for e in ctx.session_recent)


def force_probe(sp: ScoredProposal) -> Literal["INTENT", "DUP"] | None:
    """R4 sets INTENT after R3 set DUP, so a doubtful sign is asked first."""
    if not sp.intent_agreement:
        return "INTENT"
    return "DUP" if is_duplicate(sp) else None
