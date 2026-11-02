"""Probe choice (C8 step 1): argmin over eligible probes of E[residual risk after the probe] + mu x cost_turns.

The choice never sees the acceptance threshold: what to ask depends on the posteriors and the response models only (static test)."""

import math
from collections.abc import Mapping, Sequence

from lexi.common.types import ScoredProposal, SlotPosterior
from lexi.probe.menu import Probe
from lexi.risk.probe_update import REST_SPREAD, ProbeParams

MU = 0.01  # cost of one more turn, in units of risk
COST_TURNS = 1  # every probe is one question and one answer
DUP_PRIOR = SlotPosterior(entity_id=None, top="new", runner_up="same", p_top=0.5, p_second=0.5)  # nothing is known about a repeat entry
OTHERS = (0.7, 0.3)  # when a re-read is wrong it names the runner-up 70% of the time (REASK model)


def _joint(slots: Mapping[str, SlotPosterior]) -> float:
    return math.prod(s.p_top for s in slots.values())


def _contrast(slots: Mapping[str, SlotPosterior], key: str, p: ProbeParams, kind: str) -> float:
    """Exact Bayes with answer likelihoods (1 - eps, eps) for the two options and a coin flip for a truth outside both: the expected maximum
    posterior is max(.) over each of the two answers (E[max posterior] >= max prior, so a probe never looks worse than not asking)."""
    s, q = slots[key], _joint({k: v for k, v in slots.items() if k != key})
    u = p.p_unclear[kind] + p.p_silence  # the answer says nothing
    unseen = max(1.0 - s.p_top - s.p_second, 0.0) / (2 * REST_SPREAD)
    best = max(s.p_top * (1 - p.eps_c), s.p_second * p.eps_c, unseen) + max(s.p_top * p.eps_c, s.p_second * (1 - p.eps_c), unseen)
    return u * (1 - q * s.p_top) + (1 - u) * (1 - q * best)


def _readback(slots: Mapping[str, SlotPosterior], p: ProbeParams) -> float:
    """Yes/no on the whole action: P(yes | right) = 1 - eta, P(yes | wrong) = kappa. After yes the action is committed as read; after no
    the runner-up of the riskiest slot takes over."""
    j = _joint(slots)
    worst = slots[min(slots, key=lambda k: (slots[k].p_top, k))]
    alt = worst.p_second / (1 - worst.p_top) if worst.p_top < 1 else 0.0  # P(runner-up right | the riskiest slot is wrong)
    u = p.p_unclear["READBACK"] + p.p_silence
    yes, no = j * (1 - p.eta) + (1 - j) * p.kappa, j * p.eta + (1 - j) * (1 - p.kappa)
    return u * (1 - j) + (1 - u) * (yes * (1 - j * (1 - p.eta) / yes) + no * (1 - (1 - j) * (1 - p.kappa) * alt / no))


def _reask(slots: Mapping[str, SlotPosterior], key: str, p: ProbeParams) -> float:
    """A re-read behaves like a second observation of accuracy q = the slot's current p_top, down-weighted by gamma (C5 step 5)."""
    s, q = slots[key], _joint({k: v for k, v in slots.items() if k != key})
    acc = min(max(s.p_top, 0.5), 0.95)
    rest = max(1.0 - s.p_top - s.p_second, 0.0)
    prior = (s.p_top, s.p_second, rest)
    lik = [(acc, (1 - acc) * OTHERS[0], (1 - acc) * OTHERS[1]), ((1 - acc) * OTHERS[0], acc, (1 - acc) * OTHERS[1]), ((1 - acc) / 2, (1 - acc) / 2, acc)]
    unseen = rest / REST_SPREAD  # a restated value outside the top two is one specific unseen value (same weight as risk.probe_update.reask)
    expected = 0.0
    for a in range(3):  # a = what the restatement reads as: the top, the runner-up, something else
        post = [prior[0] * lik[0][a] ** p.gamma, prior[1] * lik[1][a] ** p.gamma, unseen * lik[2][a] ** p.gamma]
        expected += sum(prior[v] * lik[v][a] for v in range(3)) * (1 - q * max(post) / sum(post))
    u = p.p_unclear["REASK"] + p.p_silence
    return u * (1 - q * s.p_top) + (1 - u) * expected


def residual_risk(sp: ScoredProposal, probe: Probe, p: ProbeParams) -> float:
    """E[residual action risk after the probe] = sum over answers a of P(a) x (1 - max_v P(v | a) x product of the other slots)."""
    if probe.slot == "dup":
        return _contrast({"dup": DUP_PRIOR}, "dup", p, "CONTRAST") if probe.kind == "CONTRAST" else _readback({"dup": DUP_PRIOR}, p)
    if probe.kind == "READBACK":
        return _readback(sp.slots, p)
    if probe.kind == "REASK":
        return _reask(sp.slots, probe.slot, p)
    return _contrast(sp.slots, probe.slot, p, probe.kind)


def choose_probe(sp: ScoredProposal, eligible: Sequence[Probe], params: ProbeParams) -> Probe:
    return min(eligible, key=lambda pr: residual_risk(sp, pr, params) + MU * COST_TURNS)
