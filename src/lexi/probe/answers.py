"""Answer grammar (C8 step 3): the EB of an answer turn -> what the caller meant. Deterministic; reads CUE and value entities only."""

from dataclasses import dataclass

from lexi.common.types import Entity, EvidenceBundle, OutcomeKind, ProbeSpec

INTENT_CUES = {"CREDIT", "DEBIT", "PROMISE", "RESCHEDULE"}
SIGN_OF = {"CREDIT": "CREDIT_ENTRY", "DEBIT": "DEBIT_ENTRY"}
UNDO_CUES = {"WRONG", "NO", "UNDO", "NEG"}  # galat, nahi, ruko, cancel (and the Tamil words) inside the undo window
ETYPE = {"party": "PARTY", "amount": "AMOUNT", "date": "DATE"}
DIGIT_CHOICE = {1: 0, 2: 1}  # "ek" / "do" pick the first / second option


@dataclass(frozen=True, slots=True)
class Answer:
    kind: OutcomeKind
    chosen_index: int | None = None
    value_entity_id: str | None = None


def is_undo(eb: EvidenceBundle) -> bool:
    return any(e.cue and e.cue.cls in UNDO_CUES for e in eb.lattice.entities)


def answer_conf(eb: EvidenceBundle) -> float | None:
    """Minimum word confidence over the answer tokens."""
    confs = [w.conf for w in eb.words if w.conf is not None]
    return min(confs) if confs else None


def parse(eb: EvidenceBundle, spec: ProbeSpec) -> Answer:
    if eb.asr_status in ("EMPTY", "SILENCE") or not eb.words:
        return Answer("SILENCE")
    cues = {e.cue.cls for e in eb.lattice.entities if e.cue}
    values = [e for e in eb.lattice.entities if e.type != "CUE"]
    if eb.asr_status != "OK":
        return Answer("UNCLEAR")
    if cues & INTENT_CUES and any(e.type in ("PARTY", "AMOUNT") for e in values):  # a whole new command, not an answer
        return Answer("CONTINUE")
    if spec.kind == "READBACK":
        return _yes_no(cues, values)
    if spec.kind == "REASK":
        ent = _of_slot(values, spec.slot)
        return Answer("VALUE", value_entity_id=ent.id) if ent else Answer("UNCLEAR")
    return _choice(cues, values, spec)


def _yes_no(cues: set[str], values: list[Entity]) -> Answer:
    yes, no = bool(cues & {"YES", "RIGHT"}), bool(cues & {"NO", "WRONG"})
    if yes == no:
        return Answer("UNCLEAR")
    if yes:
        return Answer("YES")
    ent = next((e for e in values), None)  # "nahi, pachaas hazaar": a correction that carries the right value
    return Answer("NO_WITH_VALUE", value_entity_id=ent.id) if ent else Answer("NO")


def _choice(cues: set[str], values: list[Entity], spec: ProbeSpec) -> Answer:
    opts = spec.options
    if spec.kind == "INTENT_CONTRAST":
        named = {SIGN_OF[c] for c in cues & SIGN_OF.keys()}
        if len(named) == 1:
            return Answer("CHOICE", opts.index(next(iter(named))))
    elif spec.slot == "dup":
        for cls, word in (("NEW", "new"), ("SAME", "same")):
            if cls in cues and not cues & ({"SAME", "NEW"} - {cls}):
                return Answer("CHOICE", opts.index(word))
    first, second = "ORD1" in cues, "ORD2" in cues
    if first != second:
        return Answer("CHOICE", 1 if second else 0)
    ent = _of_slot(values, spec.slot)
    if ent is not None and ent.candidates:
        top = str(ent.candidates[0].value)
        if top in opts:  # the caller restated one of the options
            return Answer("CHOICE", opts.index(top))
        if spec.slot != "amount" or ent.candidates[0].value not in DIGIT_CHOICE:
            return Answer("VALUE", value_entity_id=ent.id)
    amount = next((e for e in values if e.type == "AMOUNT" and e.candidates and e.candidates[0].value in DIGIT_CHOICE and e.span[1] - e.span[0] == 1), None)
    if amount is not None:  # a bare "ek" / "do"
        return Answer("CHOICE", DIGIT_CHOICE[int(amount.candidates[0].value)])
    return Answer("UNCLEAR")


def _of_slot(values: list[Entity], slot: str) -> Entity | None:
    return next((e for e in values if e.type == ETYPE.get(slot) and e.candidates), None)
