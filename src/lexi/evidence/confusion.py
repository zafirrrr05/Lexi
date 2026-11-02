"""Acoustic confusion (C3 step 3): P(spoken | heard, bandwidth_class, cqi) over numeric lexemes.

Learned tables (fit_confusion.py, versioned) when a heard token was seen often enough in that cell;
otherwise a phonetic-distance prior: neighbours of a token share a mass that grows as the line degrades
and as the word confidence falls.
"""

import json
from pathlib import Path
from typing import Any

from lexi.evidence.normalize import Lexicon

PRIOR_MASS = {"Q1": 0.04, "Q2": 0.12, "Q3": 0.30}
MIN_SEEN = 8  # a learned row needs this many observations of the heard token
MAX_NEIGHBORS = 4


class Confusion:
    def __init__(self, tables: dict[str, Any] | None = None) -> None:
        self.tables = tables or {}
        self.version: str = self.tables.get("version", "conf-prior")
        self.repair_post: float = self.tables.get("repair_post", 0.85)

    @classmethod
    def load(cls, path: Path | None) -> "Confusion":
        return cls(json.loads(path.read_text(encoding="utf-8")) if path else None)

    def expand(self, lex: Lexicon, key: str, conf: float | None, bandwidth: str, cqi: str) -> list[tuple[str, float]]:
        """[(spoken key, p)] with the heard key first; probabilities sum to 1."""
        row = self.tables.get("tok", {}).get(f"{lex.lang}.{bandwidth}.{cqi}", {}).get(key)
        if row and row["n"] >= MIN_SEEN:
            ranked = sorted(row["p"].items(), key=lambda kv: (-kv[1], kv[0]))[: MAX_NEIGHBORS + 1]
            total = sum(p for _, p in ranked)
            return [(k, p / total) for k, p in ranked]
        near = sorted(lex.neighbors.get(key, {}).items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_NEIGHBORS]
        if not near:
            return [(key, 1.0)]
        mass = PRIOR_MASS[cqi] * (1.25 if bandwidth == "NB" else 1.0)
        if conf is not None:
            mass = max(mass * 0.5, 1.0 - conf)
        mass = min(mass, 0.6)
        weight = sum(s for _, s in near)
        return [(key, 1.0 - mass)] + [(k, mass * s / weight) for k, s in near]
