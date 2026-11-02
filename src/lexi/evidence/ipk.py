"""IPK, the internal phonetic key (C3 step 1): Devanagari, Tamil and Latin spellings of one word fold to one coarse Latin string.

Deliberately coarse: long/short vowels merge, voiced/voiceless pairs merge, aspiration and silent h vanish. Two words sound
alike when their keys are within edit distance 1; the confusion tables (confusion.py) then say how often."""

import re
import unicodedata
from functools import lru_cache

_DEVA_V = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri"}
_DEVA_M = {"ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri", "ॉ": "o", "ॅ": "e"}
_DEVA_C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th",
           "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
           "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ळ": "l"}
_DEVA_NUKTA = {"ज": "z", "फ": "f", "ड": "r", "ढ": "rh", "क": "q", "ख": "kh", "ग": "g"}
_TAMIL_V = {"அ": "a", "ஆ": "aa", "இ": "i", "ஈ": "ii", "உ": "u", "ஊ": "uu", "எ": "e", "ஏ": "ee", "ஐ": "ai", "ஒ": "o", "ஓ": "oo", "ஔ": "au"}
_TAMIL_M = {"ா": "aa", "ி": "i", "ீ": "ii", "ு": "u", "ூ": "uu", "ெ": "e", "ே": "ee", "ை": "ai", "ொ": "o", "ோ": "oo", "ௌ": "au"}
_TAMIL_C = {"க": "k", "ங": "ng", "ச": "ch", "ஞ": "n", "ட": "t", "ண": "n", "த": "th", "ந": "n", "ப": "p", "ம": "m", "ய": "y", "ர": "r", "ல": "l",
            "வ": "v", "ழ": "zh", "ள": "l", "ற": "r", "ன": "n", "ஜ": "j", "ஷ": "sh", "ஸ": "s", "ஹ": "h"}
_VIRAMA = {"्", "்"}
_NASAL = {"ं": "n", "ँ": "n", "ः": "h", "ஃ": "h"}
_VOWEL_FOLD = [("ai", "e"), ("au", "o"), ("ou", "o"), ("ei", "e"), ("ea", "i"), ("ie", "i"), ("aa", "a"), ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u")]
_DIGRAPH = [("chh", "C"), ("ch", "C"), ("sh", "s"), ("kh", "k"), ("gh", "g"), ("jh", "j"), ("th", "t"), ("dh", "d"), ("ph", "f"), ("bh", "b"),
            ("zh", "l"), ("ck", "k"), ("rh", "r")]
_VOICING = str.maketrans("bdgjvwzqyc", "ptkCfvCkik")


def _indic(text: str, vowels: dict[str, str], matras: dict[str, str], cons: dict[str, str], nukta: dict[str, str]) -> str:
    out: list[str] = []
    inherent = False  # last emitted piece is an inherent 'a' (deleted at word end, as Hindi does)
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in cons:
            base = nukta[ch] if i + 1 < len(text) and text[i + 1] == "़" and ch in nukta else cons[ch]
            i += 2 if base is not cons[ch] and text[i + 1] == "़" else 1
            nxt = text[i] if i < len(text) else ""
            if nxt in matras:
                out += [base, matras[nxt]]
                i += 1
                inherent = False
            elif nxt in _VIRAMA:
                out.append(base)
                i += 1
                inherent = False
            else:
                out += [base, "a"]
                inherent = True
            continue
        if ch in vowels:
            out.append(vowels[ch])
        elif ch in _NASAL:
            out.append(_NASAL[ch])
        elif ch.isascii() and ch.isalpha():
            out.append(ch)
        inherent = False
        i += 1
    if inherent and len(out) > 2:
        out.pop()
    return "".join(out)


@lru_cache(maxsize=8192)
def ipk(text: str, lang: str | None = None) -> str:
    t = unicodedata.normalize("NFC", text).lower()
    if any("ऀ" <= c <= "ॿ" for c in t):
        t = _indic(t, _DEVA_V, _DEVA_M, _DEVA_C, _DEVA_NUKTA)
    elif any("஀" <= c <= "௿" for c in t):
        t = _indic(t, _TAMIL_V, _TAMIL_M, _TAMIL_C, {})
    t = re.sub(r"[^a-z]", "", t)
    for a, b in _DIGRAPH:
        t = t.replace(a, b)
    t = t.replace("x", "ks").replace("c", "k")
    for a, b in _VOWEL_FOLD:
        t = t.replace(a, b)
    t = t.translate(_VOICING).replace("h", "").replace("C", "c")
    t = re.sub(r"(.)\1+", r"\1", t)
    return t[:-1] if len(t) > 3 and t.endswith("a") else t


_VOWELS = set("aeiou")


@lru_cache(maxsize=65536)
def lev(a: str, b: str) -> int:
    """Plain edit distance on IPKs: 'valid lexemes within edit distance <= 1' (C3 step 3)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@lru_cache(maxsize=65536)
def wdist(a: str, b: str) -> float:
    """Weighted edit distance in [0, 1] for party matching: vowels are cheap to confuse, dropped vowels are cheap to lose."""
    def sub(x: str, y: str) -> float:
        return 0.0 if x == y else 0.4 if x in _VOWELS and y in _VOWELS else 1.0

    def gap(x: str) -> float:
        return 0.5 if x in _VOWELS else 1.0
    prev = [0.0]
    for cb in b:
        prev.append(prev[-1] + gap(cb))
    for ca in a:
        cur = [prev[0] + gap(ca)]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + gap(ca), cur[j - 1] + gap(cb), prev[j - 1] + sub(ca, cb)))
        prev = cur
    return prev[-1] / max(len(a), len(b), 1)
