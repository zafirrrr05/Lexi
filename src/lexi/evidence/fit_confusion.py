"""Fit the confusion tables and the repair weight from Twin training records (C3 step 3-4, TrainingData kind="confusion")."""

from collections import Counter, defaultdict
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from lexi.common.hashing import digest
from lexi.evidence.confusion import MIN_SEEN
from lexi.evidence.ipk import ipk
from lexi.evidence.normalize import LEXICON_DIR, NUMERIC, Lexicon

PRIOR_POST, PRIOR_N = 0.85, 10  # repair weight shrinks toward the default while data is thin


def fit(records: Iterable[dict[str, Any]], lexicon_dir: Path | None = None) -> dict[str, Any]:
    lexicons: dict[str, Lexicon] = {}
    counts: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    post = n_rep = 0
    for r in records:
        if r["type"] == "repair":
            post, n_rep = post + bool(r["post_correct"]), n_rep + 1
            continue
        lex = lexicons.setdefault(r["lang"], Lexicon(r["lang"], lexicon_dir or LEXICON_DIR))
        spoken = [ipk(t, lex.lang) for t in r["spoken"]]
        heard = [ipk(h["w"], lex.lang) for h in r["heard"]]
        cell = counts[f"{lex.lang}.{r['bandwidth_class']}.{r['cqi']}"]
        for op, a0, a1, b0, b1 in SequenceMatcher(None, spoken, heard, autojunk=False).get_opcodes():
            if op == "equal" or (op == "replace" and a1 - a0 == b1 - b0):
                for s, h in zip(spoken[a0:a1], heard[b0:b1], strict=True):
                    if any(e.kind in NUMERIC for e in lex.lookup((s,))) and any(e.kind in NUMERIC for e in lex.lookup((h,))):
                        cell[h][s] += 1
    tok = {cell: {h: {"n": sum(c.values()), "p": {s: n / sum(c.values()) for s, n in sorted(c.items())}}
                  for h, c in sorted(rows.items()) if sum(c.values()) >= MIN_SEEN} for cell, rows in sorted(counts.items())}
    tables: dict[str, Any] = {"repair_post": min(0.97, max(0.5, (post + PRIOR_POST * PRIOR_N) / (n_rep + PRIOR_N))), "tok": tok}
    tables["version"] = "conf-" + digest(tables)[:10]
    return tables
