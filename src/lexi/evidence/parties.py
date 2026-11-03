"""PARTY entities (C3 step 2): windows over unknown tokens matched to the account's registry by weighted edit distance on IPK."""

import math

from lexi.common.types import Candidate, PartyView
from lexi.evidence.ipk import ipk, wdist
from lexi.evidence.lattice import Found
from lexi.evidence.normalize import Seq

K = 5
EPS_OOV = 0.02
MAX_WINDOW = 3
SHARPNESS = 6.0  # p ~ exp(-SHARPNESS * distance)
MIN_SIM_UNANCHORED = 0.6
OOV = "OUT_OF_REGISTRY"


def _keys(p: PartyView) -> list[str]:
    return sorted({ipk(x).replace(" ", "") for x in [p.display, *p.aliases]} | {p.ipk.replace(" ", "")})


def party_candidates(window: str, registry: list[PartyView]) -> tuple[list[Candidate], float]:
    """Top-K registry matches plus OUT_OF_REGISTRY; second value is the best similarity."""
    if not registry:
        return [Candidate(value=OOV, p=1.0)], 0.0
    dist = {p.id: min(wdist(window, k) for k in _keys(p)) for p in registry}
    ranked = sorted(dist.items(), key=lambda kv: (kv[1], kv[0]))[:K]
    best = 1.0 - ranked[0][1]
    w = {pid: math.exp(-SHARPNESS * d) for pid, d in ranked}
    w[OOV] = EPS_OOV + 0.5 * (1.0 - best) ** 2
    z = sum(w.values())
    return [Candidate(value=v, p=x / z) for v, x in sorted(w.items(), key=lambda kv: (-kv[1], kv[0]))], best


def parties(seq: Seq, registry: list[PartyView]) -> list[Found]:
    covered = {i + k for i, ms in seq.multi.items() for n, _ in ms for k in range(n)}
    unknown = [bool(t.key) and t.digits is None and not t.entries and t.i not in covered for t in seq.toks]
    out: list[Found] = []
    i = 0
    while i < len(seq.toks):
        if not unknown[i]:
            i += 1
            continue
        j = i
        while j < len(seq.toks) and unknown[j]:
            j += 1
        before = seq.toks[i - 1] if i > 0 else None
        after = seq.toks[j] if j < len(seq.toks) else None
        honor_after = bool(after and seq.has(after.i, "honor"))
        anchored = honor_after or bool(before and seq.has(before.i, "honor")) or bool(after and seq.has(after.i, "func", fkind="role"))
        best: tuple[float, int, int, list[Candidate]] | None = None
        for a in range(i, j):
            for b in range(a + 1, min(j, a + MAX_WINDOW) + 1):
                cands, sim = party_candidates(ipk(" ".join(t.w for t in seq.toks[a:b])).replace(" ", ""), registry)
                if best is None or (sim, b - a) > (best[0], best[2] - best[1]):
                    best = (sim, a, b, cands)
        if best and (anchored or best[0] >= MIN_SIM_UNANCHORED):
            sim, a, b, cands = best
            top_other = sum(c.p for c in cands if c.value != OOV) - cands[0].p if cands[0].value != OOV else 0.0
            out.append(Found("PARTY", a, b + 1 if honor_after and b == j else b, cands, mass=top_other))
        i = j
    return out
