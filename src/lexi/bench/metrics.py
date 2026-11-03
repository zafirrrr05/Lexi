"""Interval estimates of C15 (16.2): Clopper-Pearson for rates, a bootstrap over script_id for everything derived. The only place a number gets a width."""

from collections.abc import Callable, Sequence
from typing import TypeVar

import numpy as np
from scipy.stats import beta

T = TypeVar("T")


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Exact two-sided interval for k events in n trials."""
    if n == 0:
        return 0.0, 1.0
    a = (1 - conf) / 2
    return (0.0 if k == 0 else float(beta.ppf(a, k, n - k + 1)), 1.0 if k == n else float(beta.ppf(1 - a, k + 1, n - k)))


def bootstrap(items: Sequence[T], group: Callable[[T], str], stat: Callable[[list[T]], float], rng: np.random.Generator, n_boot: int = 400) -> tuple[float, float]:
    """95% percentile interval of `stat`, resampling whole scripts: variants of one script are not independent draws (4.7).
    ponytail: percentile bootstrap; BCa if a published interval ever sits on a skewed statistic."""
    by: dict[str, list[T]] = {}
    for x in items:
        by.setdefault(group(x), []).append(x)
    scripts = list(by)
    if not scripts:
        return 0.0, 0.0
    draws = [stat([x for i in rng.integers(0, len(scripts), len(scripts)) for x in by[scripts[i]]]) for _ in range(n_boot)]
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def show(p: float, lo: float, hi: float, digits: int = 3) -> str:
    """'0.412 [0.381, 0.444]': how a rate and its interval enter a report group."""
    return f"{p:.{digits}f} [{lo:.{digits}f}, {hi:.{digits}f}]"
