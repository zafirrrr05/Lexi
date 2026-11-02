"""Spoken forms (C11 step 2): a canonical value becomes one of several natural ways to say it; the gold keeps the canonical number.

2500 -> "dhai hazaar" | "do hazaar paanch sau" | "pachchis sau" | "2 hazaar 500" | "dhai thousand". The words come from the same lexicon C3 reads, and the
additive grammar here is the inverse of evidence/numerals.py, so the round trip (tests/property/test_twin_roundtrip.py) holds by construction.
"""

import random
from dataclasses import dataclass, replace
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from lexi.common.catalog import CONFIG_DIR
from lexi.common.dates import day_of_month, month_day

NATIVE = {"hi": "deva", "ta": "tamil"}
ENGLISH_SCALES = {"hundred", "thousand"}
FIRST_VARIANT = 0.75  # the first listed spelling is the one the simulated recogniser knows best, so it is also the most common
Forms = dict[str, tuple[str, ...]]
RawForms = dict[str, list[str]]


@dataclass(frozen=True)
class SpokenToken:
    w: str  # the spelling spoken, Latin
    kind: str  # num scale frac cue party date answer honorific filler other
    forms: Forms  # per script; only the first entry of each is used
    pause_after_ms: int = 0


class Lex:
    """Lexicon files of one language, indexed for generation."""

    def __init__(self, lang: str, directory: Path = CONFIG_DIR / "lexicons") -> None:
        def load(name: str) -> list[dict[str, Any]]:
            return list(yaml.safe_load((directory / lang / f"{name}.yaml").read_text(encoding="utf-8")))

        self.lang = lang
        self.num = {int(e["value"]): e["forms"] for e in load("numerals")}
        self.scale = {int(e["value"]): e["forms"] for e in load("scales")}
        self.frac = {e["kind"]: e["forms"] for e in load("fractions")}
        self.frac_value = {e["kind"]: float(e["value"]) for e in load("fractions")}
        self.dates = load("dates")
        self.answers = {e["cls"]: e["forms"] for e in load("answers")}
        self.cues: dict[str, list[RawForms]] = {}
        for e in load("cues"):
            self.cues.setdefault(e["cls"], []).append(e["forms"])
        self.repair = [e["forms"] for e in load("repair")]
        self.groups = yaml.safe_load((directory / lang / "confusable.yaml").read_text(encoding="utf-8"))["groups"]
        self.known: dict[str, tuple[str, RawForms]] = {}  # latin spelling -> (token kind, forms), for the words of carrier phrases
        for e in load("function_words"):
            for v in e["forms"]["latin"]:
                self.known[v] = ("filler" if e["kind"] == "filler" else "other", e["forms"])
        for e in load("honorifics"):
            for v in e["forms"]["latin"]:
                self.known[v] = ("honorific", e["forms"])
        for forms_list in self.cues.values():
            for forms in forms_list:
                for v in forms["latin"]:
                    self.known.setdefault(v, ("cue", forms))
        for cls in ("YES", "NO"):
            for v in self.answers[cls]["latin"]:
                self.known.setdefault(v, ("answer", self.answers[cls]))

    def numeral(self, v: int, rng: random.Random) -> list[SpokenToken]:
        """1..99 (Tamil 21..99 are tens + unit, as the additive grammar reads them)."""
        if v in self.num:
            return pick(self.num[v], "num", rng)
        return pick(self.num[v // 10 * 10], "num", rng) + pick(self.num[v % 10], "num", rng)


@cache
def lexicon(lang: str) -> Lex:
    return Lex(lang)


def pick(forms: RawForms, kind: str, rng: random.Random) -> list[SpokenToken]:
    """One spelling of an entry, split into one token per word; other scripts use a variant with the same word count."""
    variants = forms["latin"]
    latin = variants[0] if rng.random() < FIRST_VARIANT else rng.choice(variants)
    words = latin.split()
    per_script = {"latin": words}
    for script in ("deva", "tamil"):
        same = [v.split() for v in forms.get(script, []) if len(v.split()) == len(words)]
        if same:
            per_script[script] = rng.choice(same)
    return [SpokenToken(w, kind, {s: (ws[i],) for s, ws in per_script.items()}) for i, w in enumerate(words)]


def digits(n: int) -> list[SpokenToken]:
    return [SpokenToken(str(n), "num", {"latin": (str(n),)})]


def words_to_tokens(text: str, lang: str) -> list[SpokenToken]:
    """The literal words of a carrier phrase; words the lexicon knows carry their native-script form."""
    lex = lexicon(lang)
    out = []
    for w in text.split():
        kind, forms = lex.known.get(w.lower(), ("other", {}))
        out.append(SpokenToken(w, kind, {"latin": (w,), **{s: (forms[s][0],) for s in ("deva", "tamil") if forms.get(s)}}))
    return out


# ---------------------------------------------------------------------------- amounts
def _scale(lex: Lex, s: int, rng: random.Random, mix: bool) -> list[SpokenToken]:
    forms = lex.scale[s]
    english = [v for v in forms["latin"] if v in ENGLISH_SCALES]
    if mix and english:
        w = rng.choice(english)
        return [SpokenToken(w, "scale", {"latin": (w,)})]
    return pick({**forms, "latin": [v for v in forms["latin"] if v not in ENGLISH_SCALES] or forms["latin"]}, "scale", rng)


def _scaled(lex: Lex, v: int, s: int, rng: random.Random, mix: bool) -> list[SpokenToken]:
    """"<v> <scale>"; a bare "hazaar" or "lakh" stands for one."""
    return ([] if v == 1 and rng.random() < 0.4 else lex.numeral(v, rng)) + _scale(lex, s, rng, mix)


def _hundreds(lex: Lex, h: int, rng: random.Random, mix: bool) -> list[SpokenToken]:
    if lex.lang == "ta":  # nooru, irunooru ... thollaayiram: fused numerals
        return _scale(lex, 100, rng, mix) if h == 1 else lex.numeral(h * 100, rng)
    return _scaled(lex, h, 100, rng, mix)


def _words(lex: Lex, n: int, rng: random.Random, mix: bool = False) -> list[SpokenToken]:
    lakh, rem = divmod(n, 100_000)
    th, rem = divmod(rem, 1000)
    h, t = divmod(rem, 100)
    out: list[SpokenToken] = []
    if lakh:
        out += _scaled(lex, lakh, 100_000, rng, mix)
    if th:
        out += _scaled(lex, th, 1000, rng, mix)
    if h:
        out += _hundreds(lex, h, rng, mix)
    if t:
        out += lex.numeral(t, rng)
    return out


def _sau_form(lex: Lex, n: int, rng: random.Random) -> list[SpokenToken] | None:
    """1100..9999 as "<n // 100> sau [rest]": "pandrah sau" for 1500, how traders say it."""
    if lex.lang != "hi" or not 1100 <= n <= 9999:
        return None
    return _scaled(lex, n // 100, 100, rng, False) + (lex.numeral(n % 100, rng) if n % 100 else [])


def _fraction(lex: Lex, n: int, rng: random.Random) -> list[SpokenToken] | None:
    """dedh / dhai / sawa / saade X / paune X before a scale: 1500 dedh hazaar, 2500 dhai hazaar, 3500 saade teen hazaar, 2750 paune teen hazaar."""
    for s in (100_000, 1000, 100):
        if n % (s // 4):
            continue
        q = n * 4 // s  # quarters of the scale
        whole, quarter = divmod(q, 4)
        if whole == 0 or whole > 99:
            continue
        if quarter == 2 and whole in (1, 2) and ("dedh", "dhai")[whole - 1] in lex.frac:
            return pick(lex.frac[("dedh", "dhai")[whole - 1]], "frac", rng) + _scale(lex, s, rng, False)
        if quarter == 1 and whole == 1 and "sawa" in lex.frac:
            return pick(lex.frac["sawa"], "frac", rng) + _scale(lex, s, rng, False)
        if quarter == 2 and "saade" in lex.frac:
            return pick(lex.frac["saade"], "frac", rng) + lex.numeral(whole, rng) + _scale(lex, s, rng, False)
        if quarter == 3 and "paune" in lex.frac and whole + 1 <= 99:
            return pick(lex.frac["paune"], "frac", rng) + lex.numeral(whole + 1, rng) + _scale(lex, s, rng, False)
    return None


def _mixed(lex: Lex, n: int, rng: random.Random) -> list[SpokenToken] | None:
    """Digits with scale words, code-mixed: "2 hazaar 500", "25 sau", "dhai thousand" (fraction with an English scale)."""
    if n < 1000 or n >= 100_000:
        return None
    return digits(n // 1000) + _scale(lex, 1000, rng, True) + (digits(n % 1000) if n % 1000 else [])


def verbalize_amount(n: int, lang: str, rng: random.Random) -> list[SpokenToken]:
    lex = lexicon(lang)
    style = rng.choices(["words", "sau", "fraction", "digits", "mixed"], [5, 2, 2, 1, 1.5])[0]
    if style == "digits":
        return digits(n)
    make = {"sau": _sau_form, "fraction": _fraction, "mixed": _mixed}.get(style)
    return (make(lex, n, rng) if make else None) or _words(lex, n, rng)


def mid_number_pause(tokens: list[SpokenToken], ms: int) -> list[SpokenToken]:
    """A pause after the first scale word that more number words follow: the case numeric-aware endpointing exists for (C1 step 3)."""
    for i, t in enumerate(tokens[:-1]):
        if t.kind == "scale" and tokens[i + 1].kind == "num":
            return [*tokens[:i], replace(t, pause_after_ms=ms), *tokens[i + 1:]]
    return tokens


# ---------------------------------------------------------------------------- dates, answers, cues
def _day(lex: Lex, d: int, rng: random.Random) -> list[SpokenToken]:
    return digits(d) if rng.random() < 0.2 else lex.numeral(d, rng)


def verbalize_date(d: date, today: date, lang: str, rng: random.Random, ko: bool = True) -> list[SpokenToken]:
    """A spoken form that C3 resolves back to `d` against `today` (day-of-month and month rules are common/dates.py). `ko` allows "<day> ko"."""
    lex = lexicon(lang)
    delta = (d - today).days
    direct: list[list[SpokenToken]] = []
    for e in lex.dates:
        if (e["key"] == "REL" and e["days"][0] == delta) or (e["key"] == "WEEKDAY" and 1 <= delta <= 7 and e["weekday"] == d.weekday()):
            direct.append(pick(e["forms"], "date", rng))
    marked: list[list[SpokenToken]] = []
    if day_of_month(d.day, today) == d:
        marked.append(_day(lex, d.day, rng) + pick(next(e["forms"] for e in lex.dates if e["key"] == "TARIKH"), "date", rng))
        if lang == "hi" and ko:
            marked.append(_day(lex, d.day, rng) + [SpokenToken("ko", "other", {"latin": ("ko",), "deva": ("को",)})])
    if month_day(d.day, d.month, today) == d:
        marked.append(_day(lex, d.day, rng) + pick(next(e["forms"] for e in lex.dates if e["key"] == "MONTH" and e["month"] == d.month), "date", rng))
    return rng.choice(direct) if direct and (rng.random() < 0.6 or not marked) else rng.choice(marked)


def answer_tokens(kind: str, lang: str, rng: random.Random) -> list[SpokenToken]:
    """YES NO RIGHT WRONG ORD1 ORD2 UNDO NEW SAME."""
    return pick(lexicon(lang).answers[kind], "answer", rng)


def cue_tokens(cls: str, lang: str, rng: random.Random) -> list[SpokenToken]:
    """One cue word of an intent class (the answer to an intent contrast: "jama" / "udhaar")."""
    return pick(rng.choice(lexicon(lang).cues[cls]), "cue", rng)


def repair_tokens(lang: str, rng: random.Random) -> list[SpokenToken]:
    return [replace(t, kind="other") for t in pick(rng.choice(lexicon(lang).repair), "other", rng)]


# ---------------------------------------------------------------------------- text for the synthesiser
def tts_text(tokens: list[SpokenToken], lang: str) -> str:
    """Native script where the lexicon has it, Latin elsewhere; a pause marker after a token that asks for one (sim Timbre honours <pNNN>, live ignores it)."""
    parts = []
    for t in tokens:
        parts.append((t.forms.get(NATIVE[lang]) or t.forms["latin"])[0])
        if t.pause_after_ms:
            parts.append(f"<p{t.pause_after_ms}>")
    return " ".join(parts)
