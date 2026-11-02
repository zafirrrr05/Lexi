"""Tier 1 (C4 step 1): deterministic grammar over the entity and cue sequence."""

from dataclasses import dataclass, field

from lexi.common.catalog import Catalog
from lexi.common.types import EvidenceBundle, Intent
from lexi.parser.cues import STRONG, intent_cues

SLOT_TYPE = {"party": "PARTY", "amount": "AMOUNT", "date": "DATE"}


@dataclass(slots=True)
class Tier1:
    klass: str  # UNAMBIGUOUS | AMBIGUOUS | FAIL
    intent: Intent | None = None
    candidates: list[Intent] = field(default_factory=list)  # intents the cues allow
    strong: bool = False  # the intent rests on exactly one strong cue class
    slots: dict[str, str] = field(default_factory=dict)
    ambiguous_slots: list[str] = field(default_factory=list)


def _structure_hint(eb: EvidenceBundle) -> Intent | None:
    """No cue words: a date with an amount is a promise, a date alone a reschedule."""
    if not eb.lattice.of_type("DATE"):
        return None
    return "PROMISE_TO_PAY" if eb.lattice.of_type("AMOUNT") else "RESCHEDULE_DUE"


def tier1(eb: EvidenceBundle, catalog: Catalog) -> Tier1:
    cues = intent_cues(eb)
    strong = sorted(i for i, s in cues.items() if s >= STRONG)
    if len(strong) > 1:
        return Tier1("AMBIGUOUS", None, strong)  # conflicting strong cues
    if not strong:
        if cues:  # weak cues only: let Evon confirm
            return Tier1("AMBIGUOUS", None, sorted(cues, key=lambda i: (-cues[i], i)))
        hint = _structure_hint(eb)
        return Tier1("AMBIGUOUS" if hint else "FAIL", None, [hint] if hint else [])
    intent = strong[0]
    out = Tier1("UNAMBIGUOUS", intent, [intent], strong=True)
    for slot in catalog.actions[intent].slots:
        found = [e.id for e in eb.lattice.of_type(SLOT_TYPE[slot])]  # type: ignore[arg-type]
        if len(found) == 1:
            out.slots[slot] = found[0]
        elif found:
            out.ambiguous_slots.append(slot)
    if out.ambiguous_slots:
        out.klass = "AMBIGUOUS"
    return out
