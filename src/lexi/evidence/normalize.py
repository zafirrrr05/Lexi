"""Lexicons and tokens (C3 step 1): load a language's YAML lexicon, normalise a transcript into tokens, tag each token's lexical meanings."""

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lexi.common.catalog import CONFIG_DIR
from lexi.common.hashing import sha256_hex
from lexi.common.types import Word
from lexi.evidence.ipk import ipk, lev

LEXICON_DIR = CONFIG_DIR / "lexicons"
_FILES = {"numerals": "num", "scales": "scale", "fractions": "frac", "dates": "date", "honorifics": "honor", "cues": "cue",
          "repair": "repair", "answers": "answer", "function_words": "func"}
_DIGITS = str.maketrans("०१२३४५६७८९௦௧௨௩௪௫௬௭௮௯", "01234567890123456789")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_CURRENCY = re.compile(r"^(₹|rs\.?|inr)(?=\d)")
_STRIP = ".,;:!?\"'()[]{}।"
NUMERIC = ("num", "scale", "frac")


@dataclass(frozen=True, slots=True)
class Entry:
    kind: str  # num scale frac date honor cue repair answer func
    data: dict[str, Any]

    def __hash__(self) -> int:
        return hash((self.kind, tuple(sorted((k, str(v)) for k, v in self.data.items()))))


class Lexicon:
    def __init__(self, lang: str, directory: Path = LEXICON_DIR) -> None:
        self.lang = lang
        raw = bytearray()
        self.index: dict[tuple[str, ...], list[Entry]] = {}
        for fname, kind in _FILES.items():
            blob = (directory / lang / f"{fname}.yaml").read_bytes()
            raw += blob
            for item in yaml.safe_load(blob):
                data = {k: v for k, v in item.items() if k != "forms"}
                if kind == "func":
                    data["fkind"] = data.pop("kind")
                elif kind == "frac":
                    data["fkind"] = data.pop("kind")
                entry = Entry(kind, data)
                for script in item["forms"].values():
                    for form in script:
                        keys = tuple(ipk(t, lang) for t in form.split())
                        if entry not in self.index.setdefault(keys, []):
                            self.index[keys].append(entry)
        groups = yaml.safe_load((directory / lang / "confusable.yaml").read_bytes())["groups"]
        raw += (directory / lang / "confusable.yaml").read_bytes()
        self.version = f"lex-{lang}-{sha256_hex(bytes(raw))[:10]}"
        self.max_n = max(len(k) for k in self.index)
        self.neighbors = self._neighbors(groups)

    def _neighbors(self, groups: list[list[str]]) -> dict[str, dict[str, float]]:
        """key -> {neighbour key: strength} among numeric lexemes: edit distance 1 (1.0) and confusable groups (2.0)."""
        kinds = {k[0]: {e.kind for e in es if e.kind in NUMERIC} for k, es in self.index.items() if len(k) == 1}
        keys = sorted(k for k, ks in kinds.items() if ks)
        out: dict[str, dict[str, float]] = {k: {} for k in keys}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:  # same kind and long enough: on 2-3 letter keys one edit changes the word
                if min(len(a), len(b)) >= 4 and kinds[a] & kinds[b] and lev(a, b) == 1:
                    out[a][b] = out[b][a] = 1.0
        for g in groups:
            gk = [ipk(t, self.lang) for t in g]
            for a in gk:
                for b in gk:
                    if a != b:
                        out.setdefault(a, {})[b] = out.setdefault(b, {})[a] = 2.0
        return out

    def lookup(self, keys: tuple[str, ...]) -> list[Entry]:
        return self.index.get(keys, [])


@dataclass(slots=True)
class Tok:
    i: int
    w: str
    key: str
    t0: float
    t1: float
    conf: float | None
    script: str  # deva tamil latin digit
    digits: str | None = None  # the token read as a decimal number, if it is one
    entries: list[Entry] = field(default_factory=list)  # single-token lexical meanings


@dataclass(slots=True)
class Seq:
    toks: list[Tok]
    multi: dict[int, list[tuple[int, Entry]]]  # start index -> (n_tokens, entry) for forms of 2+ tokens

    def kinds(self, i: int) -> set[str]:
        return {e.kind for e in self.toks[i].entries}

    def has(self, i: int, kind: str, **match: Any) -> Entry | None:
        return next((e for e in self.toks[i].entries if e.kind == kind and all(e.data.get(k) == v for k, v in match.items())), None)


def _script(s: str) -> str:
    for c in s:
        if "ऀ" <= c <= "ॿ":
            return "deva"
        if "஀" <= c <= "௿":
            return "tamil"
    return "latin"


def tokenize(words: list[Word], lex: Lexicon) -> Seq:
    """One token per word, so entity spans index the transcript words directly."""
    toks: list[Tok] = []
    for i, w in enumerate(words):
        s = unicodedata.normalize("NFC", w.w).lower().translate(_DIGITS).strip(_STRIP)
        if (m := _CURRENCY.match(s)):
            s = s[m.end():]
        plain = s.replace(",", "")
        digits = plain if _NUMBER.fullmatch(plain) else None
        toks.append(Tok(i, s, ipk(s, lex.lang) if s else "", w.t0, w.t1, w.conf, "digit" if digits else _script(s), digits))
    multi: dict[int, list[tuple[int, Entry]]] = {}
    for t in toks:
        t.entries = list(lex.lookup((t.key,))) if t.digits is None else []
        for n in range(2, lex.max_n + 1):
            if t.i + n <= len(toks):
                for e in lex.lookup(tuple(x.key for x in toks[t.i:t.i + n])):
                    multi.setdefault(t.i, []).append((n, e))
    _verb_do(toks, lex)
    return Seq(toks, multi)


def _verb_do(toks: list[Tok], lex: Lexicon) -> None:
    """Hindi 'kar do' / 'likh do': after a verb, 'do' means 'give', not the numeral 2."""
    do_key, verb_keys = ipk("do"), {ipk(x) for x in ("kar", "karo", "likh", "likho", "de", "dena")}
    for t in toks[1:]:
        if t.key == do_key and toks[t.i - 1].key in verb_keys:
            t.entries = [e for e in t.entries if not (e.kind == "num" and e.data["value"] == 2)] + [Entry("func", {"fkind": "verb"})]
