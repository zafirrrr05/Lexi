"""Probe menu (C8 step 1): which probes are eligible for a PROBE decision, and what each one asks."""

from dataclasses import dataclass

from lexi.common.types import ProbeKind, ScoredProposal

SIGNED = ("CREDIT_ENTRY", "DEBIT_ENTRY")
SLOTS = ("party", "amount", "date")


@dataclass(frozen=True)
class Probe:
    kind: ProbeKind
    slot: str  # intent | party | amount | date | dup
    options: tuple[str, ...] = ()  # canonical values in spoken order (CONTRAST, INTENT_CONTRAST); empty for READBACK and REASK


def riskiest_slot(sp: ScoredProposal) -> str:
    """Lowest p_top among the slots whose value comes from audio; ties go to the first in party, amount, date order."""
    return min((k for k in SLOTS if k in sp.slots), key=lambda k: sp.slots[k].p_top)


def eligible(sp: ScoredProposal, reason: str, *, yes_no_only: bool = False) -> list[Probe]:
    """yes_no_only is ablation A3: every question becomes a read-back."""
    if reason == "DUP":
        return [Probe("READBACK", "dup")] if yes_no_only else [Probe("CONTRAST", "dup", ("new", "same")), Probe("READBACK", "dup")]
    if reason == "INTENT":
        if set(sp.ap.intent_candidates) == set(SIGNED) and not yes_no_only:
            return [Probe("INTENT_CONTRAST", "intent", SIGNED)]
        return [Probe("READBACK", "intent")]  # D-005: an unsigned intent that lacks a strong cue is confirmed, not contrasted (4.3 offers no second reading)
    slot = riskiest_slot(sp)
    top = sp.slots[slot]
    out = []
    if top.runner_up is not None and top.p_top + top.p_second >= 0.8:
        out.append(Probe("CONTRAST", slot, (str(top.top), str(top.runner_up))))
    out.append(Probe("REASK", slot))
    if all(s.p_top >= 0.9 for s in sp.slots.values()):  # a yes/no is only safe when the residual risk is already small
        out.append(Probe("READBACK", "action"))
    return [Probe("READBACK", "action")] if yes_no_only else out
