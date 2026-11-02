"""Repair resolution (C3 step 4): a marker within 6 tokens after an entity makes the next entity of the same type
the post-repair reading (weight `post`, default 0.85); the earlier one stays as the pre-repair hypothesis."""

from lexi.common.types import Candidate
from lexi.evidence.lattice import Found
from lexi.evidence.normalize import Seq

WINDOW = 6


def resolve(seq: Seq, found: list[Found], post: float) -> list[Found]:
    marks = [(t.i, t.i + 1) for t in seq.toks if seq.has(t.i, "repair")]
    marks += [(i, i + n) for i, ms in seq.multi.items() for n, e in ms if e.kind == "repair"]
    out: list[Found] = []
    for f in sorted(found, key=lambda f: (f.i, f.j)):
        prev = next((p for p in reversed(out) if p.etype == f.etype), None)
        if prev and f.i - prev.j <= WINDOW and any(prev.j <= a and b <= f.i for a, b in marks):
            cands = [Candidate(value=c.value, p=c.p * post, parse_id=0) for c in f.cands]
            cands += [Candidate(value=c.value, p=c.p * (1.0 - post), parse_id=1) for c in prev.cands]
            out[out.index(prev)] = Found(f.etype, prev.i, f.j, cands, max(prev.mass, f.mass), repaired=True)
        else:
            out.append(f)
    return out
