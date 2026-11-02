"""DATE entities (C3 step 2): relative words, weekdays, "<day> tarikh", "<day> <month>", "<day> ko"; resolved against the ledger date."""

from datetime import date, timedelta

from lexi.common.dates import day_of_month, month_day
from lexi.common.types import Candidate
from lexi.evidence.confusion import Confusion
from lexi.evidence.lattice import Found
from lexi.evidence.normalize import Entry, Lexicon, Seq
from lexi.evidence.numerals import amount_candidates, is_numeric

K = 5
ALT_READING = 0.25  # "kal" is tomorrow or yesterday; the first listed day is the primary reading


def _marker(seq: Seq, i: int) -> tuple[int, Entry] | None:
    """Longest date marker starting at token i."""
    multi = [(n, e) for n, e in seq.multi.get(i, []) if e.kind == "date"]
    if multi:
        return max(multi, key=lambda ne: ne[0])
    e = next((e for e in seq.toks[i].entries if e.kind == "date"), None)
    return (1, e) if e else None


def dates(seq: Seq, lex: Lexicon, conf: Confusion, today: date, bandwidth: str, cqi: str) -> list[Found]:
    out: list[Found] = []
    promise_ctx = any(e.kind == "cue" and e.data["cls"] in ("PROMISE", "RESCHEDULE") for t in seq.toks for e in t.entries)

    def day_readings(k: int) -> tuple[list[Candidate], float, int] | None:
        """Day of month read from the numeral(s) ending at token k; Tamil days above 19 are two words ("irubadhu irandu"). Third value: first token."""
        for n in (2, 1) if lex.lang == "ta" else (1,):
            if k - n + 1 < 0:
                continue
            toks = seq.toks[k - n + 1:k + 1]
            tens = n == 1 or any(e.kind == "num" and e.data["value"] in (20, 30) for e in toks[0].entries)  # irubadhu / muppadhu + unit
            if tens and all(is_numeric(t) for t in toks):
                cands, mass = amount_candidates(lex, conf, toks, bandwidth, cqi)
                if days := [c for c in cands if 1 <= int(c.value) <= 31]:
                    return days, mass, k - n + 1
        return None

    def emit(i: int, j: int, pairs: list[tuple[date | None, float]], mass: float) -> None:
        acc: dict[str, float] = {}
        for d, p in pairs:
            if d:
                acc[d.isoformat()] = acc.get(d.isoformat(), 0.0) + p
        if acc:
            ranked = sorted(acc.items(), key=lambda kv: (-kv[1], kv[0]))[:K]
            z = sum(p for _, p in ranked)
            out.append(Found("DATE", i, j, [Candidate(value=v, p=p / z) for v, p in ranked], mass))

    i = 0
    while i < len(seq.toks):
        m = _marker(seq, i)
        if m:
            n, e = m
            if e.data["key"] == "REL":
                days = e.data["days"]
                ps = [1 - ALT_READING, ALT_READING] if len(days) > 1 else [1.0]
                emit(i, i + n, [(today + timedelta(days=d), p) for d, p in zip(days, ps, strict=False)], 0.0)
            elif e.data["key"] == "WEEKDAY":
                emit(i, i + n, [(today + timedelta(days=(e.data["weekday"] - today.weekday()) % 7 or 7), 1.0)], 0.0)
            elif i > 0 and (dr := day_readings(i - 1)):
                cands, mass, first = dr
                if e.data["key"] == "TARIKH":
                    emit(first, i + n, [(day_of_month(int(c.value), today), c.p) for c in cands], mass)
                else:  # MONTH
                    emit(first, i + n, [(month_day(int(c.value), e.data["month"], today), c.p) for c in cands], mass)
            i += n
            continue
        nxt = seq.toks[i + 1] if i + 1 < len(seq.toks) else None
        if promise_ctx and nxt and nxt.key == "ko" and (dr := day_readings(i)) and not _marker(seq, i + 1):
            emit(i, i + 2, [(day_of_month(int(c.value), today), c.p) for c in dr[0]], dr[1])
            i += 2
            continue
        i += 1
    return out
