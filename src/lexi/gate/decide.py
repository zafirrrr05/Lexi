"""C7 decide(): the pure decision function. First matching rule wins (R0-R8); any exception fails closed to ESCALATE(GATE_ERROR).

Everything the function reads is in DecideInput, and DecideInput is recorded in the audit, so replaying a decision needs neither a clock nor
the live registry (I7)."""

from lexi.common.catalog import Catalog
from lexi.common.ids import det_id
from lexi.common.types import (
    BaselineShadow,
    CertRef,
    Decision,
    DecisionKind,
    Effective,
    Part,
    ProbeRequest,
    RegistrySnapshot,
    ScoredProposal,
)
from lexi.gate.baseline import shadow
from lexi.gate.modes import GateMode
from lexi.gate.rules import asr_block, force_probe, intent_of, limit_block, missing_slots

BOOTSTRAP_LAMBDA_MIN = 0.001  # D11a: before Valid has produced a grid, a call takes two probes unless the naive risk is below this


class DecideInput(Part):
    sp: ScoredProposal
    mode: GateMode
    now: float
    registry: RegistrySnapshot | None  # lease_expiry as seen; cells pruned to the decision cell
    registry_max_age_s: float
    scorer_version_active: str
    audit_healthy: bool
    ledger_healthy: bool
    lambda_min: float | None  # COLLECT only: smallest grid point of the cell (C7 modes)
    theta_b: float


def cell_of(sp: ScoredProposal, catalog: Catalog) -> str | None:
    """R0: fixed at stage 0 and carried in ctx.cell; the CQI of a probe-answer turn never moves it. None while no intent is known."""
    if sp.ctx is not None and sp.ctx.cell:
        return sp.ctx.cell
    intent = intent_of(sp)
    return None if intent is None else catalog.cell_key(sp.eb.lang, sp.eb.profile.cqi, catalog.tier_of(intent))


def eligibility(inp: DecideInput, catalog: Catalog) -> bool:
    """el_j of COLLECT: no R1-R4 block applies and SP_j is not degraded. Independent of lambda."""
    sp = inp.sp
    return not (asr_block(sp) or missing_slots(sp, catalog) or sp.flags.degraded or limit_block(sp, catalog) or force_probe(sp))


def decide(inp: DecideInput, catalog: Catalog) -> Decision:
    try:
        return _decide(inp, catalog)
    except Exception:  # fail closed (P1): a bug in the rules must never become a commit
        return _make(inp, catalog, None, "ESCALATE", "GATE_ERROR", base_shadow=BaselineShadow(kind="ESCALATE"))


def escalate(inp: DecideInput, catalog: Catalog, reason: str) -> Decision:
    """ESCALATE outside the rule table: act() uses it when the audit log gives no ack (C7 step 2)."""
    return _make(inp, catalog, cell_of(inp.sp, catalog), "ESCALATE", reason)


def _decide(inp: DecideInput, catalog: Catalog) -> Decision:
    sp, lim = inp.sp, catalog.limits
    ctx = sp.ctx or sp.eb.dialogue_ctx
    cell = cell_of(sp, catalog)

    def make(kind: DecisionKind, reason: str, cert: Effective | None = None, probe: str | None = None) -> Decision:
        return _make(inp, catalog, cell, kind, reason, cert=cert, probe=probe)

    def again(reason: str) -> Decision:
        spent = ctx.reprompts_used >= lim.max_reprompts or ctx.turns_used >= lim.max_turns  # 4.5: turns are bounded as well
        return make("ESCALATE", "REPROMPTS_EXHAUSTED") if spent else make("REPROMPT", reason)

    if (reason := asr_block(sp)) is not None:  # R1
        return again(reason)
    if missing_slots(sp, catalog):  # R2
        return again("MISSING_SLOT")
    if (block := limit_block(sp, catalog)) is not None:  # R3
        return make(block.kind, block.reason)
    force = force_probe(sp)  # R3 duplicate guard, R4
    can_probe = ctx.probes_used < lim.max_probes and ctx.turns_used < lim.max_turns

    if inp.mode == "COLLECT":  # produce trajectories; never commits
        if sp.flags.degraded:
            return make("ESCALATE", "NO_AUTONOMY")
        if force is None and sp.s <= (BOOTSTRAP_LAMBDA_MIN if inp.lambda_min is None else inp.lambda_min):
            return make("COMMIT", "COLLECT_STOP")
        return make("PROBE", force or "ABOVE_LAMBDA", probe=force or "ABOVE_LAMBDA") if can_probe else make("ESCALATE", "PROBES_EXHAUSTED")

    eff = _autonomy(inp, catalog, cell)  # R5
    if eff is not None and eff.lam is not None:
        if force is None and sp.s <= eff.lam:  # R6
            return make("COMMIT", "HEDGED" if eff.hedged else "CERTIFIED", cert=eff)
        if can_probe:  # R7
            return make("PROBE", force or "ABOVE_LAMBDA", probe=force or "ABOVE_LAMBDA")
        return make("ESCALATE", "PROBES_EXHAUSTED")  # R8
    return make("ESCALATE", "NO_AUTONOMY")


def _autonomy(inp: DecideInput, catalog: Catalog, cell: str | None) -> Effective | None:
    """R5: the effective threshold of the cell if autonomy is available, else None."""
    reg, sp = inp.registry, inp.sp
    if inp.mode == "COLD_START" or reg is None or cell is None or cell not in reg.cells:
        return None
    ok = (reg.lease_expiry > inp.now and inp.now - reg.ts <= inp.registry_max_age_s
          and reg.scorer_version == inp.scorer_version_active == sp.scorer_version and reg.catalog_version == catalog.version  # release pair (F9)
          and inp.audit_healthy and inp.ledger_healthy and not sp.flags.degraded)
    eff = reg.cells[cell].effective
    return eff if ok and eff.lam is not None else None


def _make(inp: DecideInput, catalog: Catalog, cell: str | None, kind: DecisionKind, reason: str, *, cert: Effective | None = None,
          probe: str | None = None, base_shadow: BaselineShadow | None = None) -> Decision:
    sp, reg = inp.sp, inp.registry
    cands = {k: [[s.top, s.p_top], [s.runner_up, s.p_second]] for k, s in sp.slots.items()} if kind in ("ESCALATE", "REFUSE") else None
    return Decision(
        trace_id=sp.trace_id, ts=inp.now, decision_id=det_id("DEC", sp.sp_id, inp.mode, kind, reason), kind=kind, reason_code=reason, cell=cell,
        cert_ref=CertRef(cert_id=cert.cert_id, rung=cert.rung or 0, lam=cert.lam or 0.0, hedged=cert.hedged) if cert else None,
        registry_version=reg.version if reg else None, lease_expiry_seen=reg.lease_expiry if reg else None, scorer_version=sp.scorer_version,
        catalog_version=catalog.version, baseline_shadow=base_shadow or shadow(sp, catalog, inp.theta_b),
        probe_request=ProbeRequest(base_sp=sp, stage_index=sp.stage_index + 1, reason=probe) if probe else None,
        escalation_payload={"candidates": cands} if cands is not None else None)
