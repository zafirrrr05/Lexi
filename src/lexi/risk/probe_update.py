"""Probe update (C5 step 5): how an answer moves the slot posteriors. Pure; works on the compact SlotPosterior of the base SP."""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from lexi.common.types import Entity, ProbeOutcome, ScoredProposal, SlotPosterior, Value

REST_SPREAD = 5  # mass outside the top two is spread over this many unseen values when a new reading arrives
FLOOR = 1e-3


class ProbeParams(BaseModel):
    """probe_models.json: response model of the caller to each probe, fitted on the Twin (C8 fit_models)."""

    version: str = "probe-default"
    eps_c: float = 0.05  # picks the wrong option of a forced choice
    eta: float = 0.05  # says "no" to a right read-back
    kappa: float = 0.25  # says "yes" to a wrong read-back (acquiescence)
    gamma: float = 0.7  # exponent on the second observation of a re-asked slot
    p_unclear: dict[str, float] = {"CONTRAST": 0.06, "READBACK": 0.04, "REASK": 0.10, "INTENT_CONTRAST": 0.06}
    p_silence: float = 0.03

    @classmethod
    def load(cls, path: Path | None) -> "ProbeParams":
        return cls(**json.loads(path.read_text(encoding="utf-8"))) if path else cls()


@dataclass(slots=True)
class Update:
    slots: dict[str, SlotPosterior]
    intent: str | None
    intent_agreement: bool
    dup_cleared: bool
    agrees: bool | None  # the answer matched the candidate that was on top before it (None: no evidence)


def _make(entity_id: str | None, items: dict[Value, float], rest: float) -> SlotPosterior:
    z = sum(items.values()) + rest
    ranked = sorted(items.items(), key=lambda kv: (-kv[1], str(kv[0])))
    p1 = ranked[0][1] / z
    return SlotPosterior(entity_id=entity_id, top=ranked[0][0], runner_up=ranked[1][0] if len(ranked) > 1 else None, p_top=p1,
                         p_second=ranked[1][1] / z if len(ranked) > 1 else 0.0)


def _items(sp: SlotPosterior) -> tuple[dict[Value, float], float]:
    items = {sp.top: sp.p_top}
    if sp.runner_up is not None:
        items[sp.runner_up] = sp.p_second
    return items, max(1.0 - sp.p_top - sp.p_second, 0.0)


def contrast(sp: SlotPosterior, chosen: Value, eps_c: float) -> SlotPosterior:
    """CONTRAST answer: chosen candidate x (1 - eps_c), the other x eps_c."""
    items, rest = _items(sp)
    return _make(sp.entity_id, {v: p * ((1 - eps_c) if v == chosen else eps_c) for v, p in items.items()}, rest)


def reask(sp: SlotPosterior, entity: Entity, gamma: float, demote_top: float = 1.0) -> SlotPosterior:
    """Normalised product of the old posterior and the new reading's candidates, exponent gamma on the new one."""
    items, rest = _items(sp)
    items[sp.top] *= demote_top
    seen = {c.value: c.p for c in entity.candidates}
    unseen = [v for v in seen if v not in items]
    w = {v: (items.get(v, rest / REST_SPREAD)) * max(seen.get(v, 0.0), FLOOR) ** gamma for v in {*items, *seen}}
    return _make(sp.entity_id, w, rest * FLOOR ** gamma if not unseen else 0.0)


def readback_yes(slots: dict[str, SlotPosterior], eta: float, kappa: float) -> dict[str, SlotPosterior]:
    """YES to a read-back of the whole action: P(yes | right) = 1 - eta, P(yes | wrong) = kappa, on the joint probability of being right."""
    joint = math.prod(s.p_top for s in slots.values())
    if joint >= 1.0 or joint <= 0.0:
        return dict(slots)
    after = joint * (1 - eta) / (joint * (1 - eta) + (1 - joint) * kappa)
    r = math.log(after) / math.log(joint)
    out = {}
    for k, s in slots.items():
        p1 = s.p_top ** r
        out[k] = s.model_copy(update={"p_top": p1, "p_second": min(s.p_second, 1 - p1)})
    return out


def readback_no(slots: dict[str, SlotPosterior]) -> dict[str, SlotPosterior]:
    """NO to a read-back: something read was wrong. The riskiest slot loses its read-back candidate."""
    worst = min(slots, key=lambda k: (slots[k].p_top, k))
    s = slots[worst]
    items, rest = _items(s)
    if s.runner_up is None:
        return {**slots, worst: s.model_copy(update={"p_top": min(s.p_top, 0.05)})}
    del items[s.top]
    return {**slots, worst: _make(s.entity_id, items, rest)}


def apply(base: ScoredProposal, o: ProbeOutcome, eps_c: float, eta: float, kappa: float, gamma: float) -> Update:
    slots = dict(base.slots)
    upd = Update(slots, base.intent, base.intent_agreement, base.dup_cleared, None)
    spec, kind = o.spec, o.kind
    if kind in ("UNCLEAR", "SILENCE", "CONTINUE", "UNDO"):
        return upd
    if spec.slot == "dup":
        new = (kind == "CHOICE" and o.chosen_index == 0) or kind == "YES"
        upd.dup_cleared = base.dup_cleared or new
        upd.agrees = new
        return upd
    if spec.kind == "INTENT_CONTRAST":
        if kind == "CHOICE" and o.chosen_index is not None and o.chosen_index < len(spec.options):
            picked = spec.options[o.chosen_index]
            upd.agrees = picked == base.intent
            upd.intent, upd.intent_agreement = picked, True
            upd.slots["intent"] = SlotPosterior(entity_id=None, top=picked, runner_up=next((x for x in spec.options if x != picked), None),
                                                p_top=1 - eps_c, p_second=eps_c)
        return upd
    if spec.kind == "READBACK":
        if kind == "YES":
            upd.slots, upd.agrees = readback_yes(slots, eta, kappa), True
            upd.intent_agreement = upd.intent_agreement or spec.slot == "intent"  # D-005: the intent probe of an unsigned intent is a yes/no read-back
        elif kind == "NO":
            upd.slots, upd.agrees = readback_no(slots), False
        elif kind == "NO_WITH_VALUE" and (ent := o.answer_eb.lattice.get(o.value_entity_id or "")) and (slot := _slot_of(ent)) in slots:
            upd.slots[slot] = reask(slots[slot], ent, gamma, demote_top=kappa)
            upd.agrees = False
        return upd
    slot = spec.slot
    if slot not in slots:
        return upd
    old_top = slots[slot].top
    if spec.kind == "CONTRAST" and kind == "CHOICE" and o.chosen_index is not None and o.chosen_index < len(spec.options):
        chosen = _match(spec.options[o.chosen_index], slots[slot])
        upd.slots[slot] = contrast(slots[slot], chosen, eps_c)
        upd.agrees = chosen == old_top
    elif kind == "VALUE" and (ent := o.answer_eb.lattice.get(o.value_entity_id or "")) is not None:
        upd.slots[slot] = reask(slots[slot], ent, gamma)
        upd.agrees = upd.slots[slot].top == old_top
    return upd


def _slot_of(e: Entity) -> str:
    return {"PARTY": "party", "AMOUNT": "amount", "DATE": "date"}.get(e.type, "")


def _match(option: str, sp: SlotPosterior) -> Value:
    """Options are canonical values as strings; map back to the posterior's typed value."""
    return next((v for v in (sp.top, sp.runner_up) if v is not None and str(v) == option), option)
