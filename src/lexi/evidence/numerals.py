"""AMOUNT entities (C3 step 2): Indian additive number grammar over numeral, scale and fraction lexemes.

Each numeric token expands into phonetic neighbours (confusion.py); the grammar runs as a beam search
(width 12). The one real ambiguity is scale grouping: in "do sau paanch hazaar" the thousand applies to
the whole group (205000, 0.7) or only to the last numeral (5200, 0.3).
"""

from collections.abc import Sequence
from typing import Any

from lexi.common.types import Candidate
from lexi.evidence.confusion import Confusion
from lexi.evidence.lattice import Found
from lexi.evidence.normalize import NUMERIC, Lexicon, Seq, Tok

BEAM = 12
K = 5
GROUP_WEIGHT = 0.7
ADJACENT_NUMERAL = 0.5  # two numerals in a row is not grammatical Hindi/Tamil; keep, but cheap
State = tuple[float, float, float, float, bool]  # total, group, run, pending saade/paune adjustment, last was numeral
Lexeme = tuple[str, float]  # ("n", value) | ("s", scale) | ("f", fraction) | ("a", adjustment)


def is_numeric(t: Tok) -> bool:
    return t.digits is not None or any(e.kind in NUMERIC for e in t.entries)


def lexemes(lex: Lexicon, conf: Confusion, t: Tok, bandwidth: str, cqi: str) -> list[tuple[Lexeme, float]]:
    """Readings of one token with probabilities: its own lexemes first, then phonetic neighbours."""
    if t.digits is not None:
        return [(("n", float(t.digits)), 1.0)]
    out: dict[Lexeme, float] = {}
    for key, p in conf.expand(lex, t.key, t.conf, bandwidth, cqi):
        entries = [e for e in lex.lookup((key,)) if e.kind in NUMERIC]
        for e in entries:
            out[_lexeme(e.kind, e.data)] = out.get(_lexeme(e.kind, e.data), 0.0) + p / len(entries)
    return sorted(out.items(), key=lambda kv: (-kv[1], kv[0]))


def _lexeme(kind: str, data: dict[str, Any]) -> Lexeme:
    if kind == "num":
        return ("n", float(data["value"]))
    if kind == "scale":
        return ("s", float(data["value"]))
    if data["fkind"] == "saade":
        return ("a", 0.5)
    if data["fkind"] == "paune":
        return ("a", -0.25)
    return ("f", float(data["value"]))


def _step(st: State, lx: Lexeme) -> list[tuple[State, float]]:
    total, group, run, adj, was_num = st
    kind, v = lx
    if kind == "n" or kind == "f":
        w = ADJACENT_NUMERAL if was_num and run > 0 else 1.0
        return [((total, group, run + v + adj, 0.0, True), w)]
    if kind == "a":
        return [((total, group, run, v, False), 1.0)]
    if v == 100:
        return [((total, group + (run or 1.0) * 100, 0.0, 0.0, False), 1.0)]
    whole = group + run or 1.0
    if group > 0 and run > 0:
        return [((total + whole * v, 0.0, 0.0, 0.0, False), GROUP_WEIGHT),
                ((total + run * v, group, 0.0, 0.0, False), 1.0 - GROUP_WEIGHT)]
    return [((total + whole * v, 0.0, 0.0, 0.0, False), 1.0)]


def amount_candidates(lex: Lexicon, conf: Confusion, toks: Sequence[Tok], bandwidth: str, cqi: str) -> tuple[list[Candidate], float]:
    """Top-K amounts for a run of tokens, and confusable_mass = 1 - prod(p of the heard reading)."""
    beam: dict[State, float] = {(0.0, 0.0, 0.0, 0.0, False): 1.0}
    p_heard = 1.0
    for t in toks:
        alts = lexemes(lex, conf, t, bandwidth, cqi)
        p_heard *= alts[0][1] if alts else 1.0
        nxt: dict[State, float] = {}
        for st, w in beam.items():
            for lx, p in alts:
                for st2, w2 in _step(st, lx):
                    nxt[st2] = nxt.get(st2, 0.0) + w * p * w2
        beam = dict(sorted(nxt.items(), key=lambda kv: (-kv[1], kv[0]))[:BEAM])
    values: dict[int, float] = {}
    for (total, group, run, _, _), w in beam.items():
        v = round(total + group + run)
        if v > 0:
            values[v] = values.get(v, 0.0) + w
    ranked = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))[:K]
    z = sum(p for _, p in ranked)
    return [Candidate(value=v, p=p / z) for v, p in ranked], 1.0 - p_heard


def amounts(seq: Seq, lex: Lexicon, conf: Confusion, bandwidth: str, cqi: str, claimed: set[int]) -> list[Found]:
    """AMOUNT spans: runs of numeric tokens, a connector ("aur") may join two runs. Tokens claimed by a DATE are skipped."""
    toks = seq.toks
    free = [is_numeric(t) and t.i not in claimed for t in toks]
    out: list[Found] = []
    i = 0
    while i < len(toks):
        if not free[i]:
            i += 1
            continue
        j = i + 1
        while j < len(toks):
            if free[j]:
                j += 1
            elif j + 1 < len(toks) and free[j + 1] and seq.has(j, "func", fkind="connector"):
                j += 2
            else:
                break
        cands, mass = amount_candidates(lex, conf, [t for t in toks[i:j] if free[t.i]], bandwidth, cqi)
        if cands:
            out.append(Found("AMOUNT", i, j, cands, mass))
        i = j
    return out
