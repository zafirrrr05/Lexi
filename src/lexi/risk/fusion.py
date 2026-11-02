"""Bayes fusion (C5 step 2): posterior_k(v) proportional to prior_k(v) x L_k(v)^tau_k, one list per critical slot."""

from collections.abc import Callable, Sequence

from lexi.common.types import Candidate, Value

FLOOR = 1e-6


def fuse(cands: Sequence[Candidate], prior: Callable[[Value], float], tau: float = 1.0) -> list[tuple[Value, float]]:
    """Candidates of the same value (repair hypotheses) add up; result sorted by probability, ties by value."""
    like: dict[Value, float] = {}
    for c in cands:
        like[c.value] = like.get(c.value, 0.0) + c.p
    w = {v: max(prior(v), FLOOR) * max(p, FLOOR) ** tau for v, p in like.items()}
    z = sum(w.values())
    return sorted(((v, x / z) for v, x in w.items()), key=lambda kv: (-kv[1], str(kv[0])))
