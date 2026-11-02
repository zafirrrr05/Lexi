"""Entity lattice assembly (C3 step 7): spans found by the grammars plus CUE entities, ids E1.. in span order."""

from dataclasses import dataclass, field

from lexi.common.types import Candidate, Cue, Entity, EntityFeatures, Lattice, Mode
from lexi.evidence.normalize import Seq

_RANK = {"AMOUNT": 0, "DATE": 1, "PARTY": 2, "CUE": 3}


@dataclass(slots=True)
class Found:
    etype: str  # AMOUNT DATE PARTY CUE
    i: int  # first token
    j: int  # one past the last token
    cands: list[Candidate] = field(default_factory=list)
    mass: float = 0.0  # confusable_mass
    repaired: bool = False
    cue: Cue | None = None


def cues(seq: Seq, mode: Mode) -> list[Found]:
    """One CUE entity per class a token (or multi-token form) carries. Answer words only outside NEW_COMMAND."""
    out: dict[tuple[str, int, int], Found] = {}

    def add(cls: str, strength: float, i: int, j: int) -> None:
        out.setdefault((cls, i, j), Found("CUE", i, j, cue=Cue(cls=cls, strength=strength)))

    for t in seq.toks:
        spans = [(1, e) for e in t.entries] + list(seq.multi.get(t.i, []))
        for n, e in spans:
            if e.kind == "cue":
                add(e.data["cls"], float(e.data["strength"]), t.i, t.i + n)
            elif e.kind == "repair":
                add("REPAIR", 1.0, t.i, t.i + n)
            elif e.kind == "answer" and mode != "NEW_COMMAND":
                add(e.data["cls"], 1.0, t.i, t.i + n)
    return list(out.values())


def assemble(seq: Seq, found: list[Found], truncated_last: bool) -> Lattice:
    ents: list[Entity] = []
    last = len(seq.toks)
    for f in sorted(found, key=lambda f: (f.i, f.j, _RANK[f.etype], f.cue.cls if f.cue else "")):
        toks = seq.toks[f.i:f.j]
        confs = [t.conf for t in toks if t.conf is not None]
        ents.append(Entity(
            id=f"E{len(ents) + 1}", type=f.etype, span=(f.i, f.j), t0=toks[0].t0, t1=toks[-1].t1,
            surface=" ".join(t.w for t in toks), candidates=f.cands, cue=f.cue,
            features=EntityFeatures(
                min_conf=min(confs) if confs else None, mean_conf=sum(confs) / len(confs) if confs else None,
                n_lexemes=len(toks), confusable_mass=f.mass, repaired=f.repaired,
                truncated=truncated_last and f.j == last and f.etype in ("AMOUNT", "DATE"))))
    return Lattice(entities=ents)
