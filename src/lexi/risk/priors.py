"""Ledger priors (C5 step 2): what this party usually says. Pure functions of the party's ledger slice."""

import math
from datetime import date

from lexi.common.types import PartyContext

KERNEL_WIDTH = 0.35  # log-scale bandwidth of the amount density
HISTORY_WEIGHT = 0.6  # 0.6 x density over past amounts + 0.4 x log-uniform
OUT_OF_RANGE = 0.05  # log-uniform leakage outside 100..hard_max
OOV_PRIOR = 0.02
DAY = 86400.0


def amount_prior(value: int, history: list[int], hard_max: int) -> float:
    lu = 1.0 / math.log(hard_max / 100)
    u = lu if 100 <= value <= hard_max else lu * OUT_OF_RANGE
    hist = [h for h in history if h > 0]
    if not hist:
        return u
    z = math.log(max(value, 1))
    k = sum(math.exp(-0.5 * ((z - math.log(h)) / KERNEL_WIDTH) ** 2) / (KERNEL_WIDTH * math.sqrt(2 * math.pi)) for h in hist) / len(hist)
    return HISTORY_WEIGHT * k + (1 - HISTORY_WEIGHT) * u


def date_prior(iso: str, today: str, last_entry_ts: float | None) -> float:
    """Near-future days are likelier; the weekday of the party's last entry is a little likelier."""
    d, t = date.fromisoformat(iso), date.fromisoformat(today)
    days = (d - t).days
    w = 0.02 if days < 0 else math.exp(-days / 21) + 0.05
    if last_entry_ts is not None and date.fromtimestamp(last_entry_ts).weekday() == d.weekday():
        w *= 1.25
    return w


def party_prior(ctx: PartyContext | None, now: float) -> float:
    """Recency and frequency of interaction; out-of-registry gets a small constant."""
    if ctx is None:
        return OOV_PRIOR
    recency = 0.3 + 0.7 * math.exp(-max(now - ctx.last_entry_ts, 0.0) / DAY / 30) if ctx.last_entry_ts is not None else 0.3
    return recency * (0.5 + 0.5 * min(len(ctx.amount_hist) / 20, 1.0))
