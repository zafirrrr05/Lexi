"""Digit verbalizer (C8 step 2): amounts and dates become words, and an amount that holds a confusable number is echoed digit by digit."""

from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"
SCALES = (10_000_000, 100_000, 1000)


@cache
def _tables(directory: Path = PROMPTS_DIR) -> dict[str, Any]:
    return yaml.safe_load((directory / "verbalizer.yaml").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _below_100(n: int, t: dict[str, Any]) -> list[str]:
    nums = t["numerals"]
    return [nums[n]] if n in nums else [nums[n // 10 * 10], nums[n % 10]]  # ta: tens + unit


def _words(n: int, lang: str, directory: Path) -> list[str]:
    t = _tables(directory)[lang]
    if n == 0:
        return [t["numerals"][0]]
    out: list[str] = []
    for scale in SCALES:
        q, n = divmod(n, scale)
        if q:
            out += [*_words(q, lang, directory), t["scales"][scale]]
    h, r = divmod(n, 100)
    if h:
        out += [t["scales"][100]] if lang == "ta" and h == 1 else [t["hundreds"][h * 100]] if lang == "ta" else [t["numerals"][h], t["scales"][100]]
    return out + (_below_100(r, t) if r else [])


def _parts(n: int) -> list[tuple[int, str | None]]:
    """(number, scale) pairs of an amount in Indian grouping: 35000 -> [(35, 'k')]; the scale is its power of ten as a key."""
    out: list[tuple[int, str | None]] = []
    for scale in SCALES:
        q, n = divmod(n, scale)
        if q:
            out.append((q, str(scale)))
    h, r = divmod(n, 100)
    return out + ([(h, "100")] if h else []) + ([(r, None)] if r else [])


def spoken_amount(amount: int, lang: str, directory: Path = PROMPTS_DIR) -> str:
    return " ".join(_words(amount, lang, directory))


def is_confusable(amount: int, directory: Path = PROMPTS_DIR) -> bool:
    pairs = {x for p in _tables(directory)["confusable"] for x in p}
    return any(q in pairs for q, scale in _parts(amount) if scale != "100")


def digit_echo(amount: int, lang: str, directory: Path = PROMPTS_DIR) -> str:
    """'teen, paanch, hazaar': each number of the amount read digit by digit, followed by its scale word."""
    t = _tables(directory)[lang]
    digits = [t["numerals"][d] for d in range(10)]
    out: list[str] = []
    for q, scale in _parts(amount):
        out += [digits[int(c)] for c in str(q)]
        if scale:
            out.append(t["scales"][int(scale)])
    return ", ".join(out)


def amount_phrase(amount: int, lang: str, directory: Path = PROMPTS_DIR) -> tuple[str, bool]:
    """(what to say, whether a digit echo was appended). Spec C8 step 2: 'paintees hazaar - teen, paanch, hazaar'."""
    words = spoken_amount(amount, lang, directory)
    return (f"{words} - {digit_echo(amount, lang, directory)}", True) if is_confusable(amount, directory) else (words, False)


def spoken_date(iso: str, lang: str, directory: Path = PROMPTS_DIR) -> str:
    d = date.fromisoformat(iso)
    return f"{spoken_amount(d.day, lang, directory)} {_tables(directory)['months'][d.month - 1]}"
