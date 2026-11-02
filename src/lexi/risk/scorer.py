"""C5 Risk Engine: (EB, AP) -> ScoredProposal at stage 0, (ProbeOutcome) -> ScoredProposal at stage j+1. Pure; keeps no session state."""

import math
from collections.abc import Callable

from lexi.common.catalog import Catalog
from lexi.common.hashing import digest
from lexi.common.ids import det_id
from lexi.common.types import (
    ActionProposal,
    DialogueCtx,
    EntityFeatures,
    EvidenceBundle,
    Intent,
    ProbeOutcome,
    ScoredProposal,
    SlotPosterior,
    SpFlags,
    Value,
)
from lexi.risk import features as F
from lexi.risk.bundle import ScorerBundle
from lexi.risk.calibrate import S_FLOOR
from lexi.risk.fusion import fuse
from lexi.risk.priors import amount_prior, date_prior, party_prior
from lexi.risk.probe_update import ProbeParams, apply

SLOT_TYPE = {"party": "PARTY", "amount": "AMOUNT", "date": "DATE"}
OOV = "OUT_OF_REGISTRY"


class RiskEngine:
    def __init__(self, catalog: Catalog, bundle: ScorerBundle | None, probe: ProbeParams, *, ablate: frozenset[str] = frozenset()) -> None:
        self.catalog, self.bundle, self.probe, self.ablate = catalog, bundle, probe, ablate
        self.scorer_version = bundle.version if bundle else "bootstrap-naive"

    # ---- stage 0
    def score(self, eb: EvidenceBundle, ap: ActionProposal) -> ScoredProposal:
        if ap.fail or ap.intent is None or eb.flags.asr_failed or eb.flags.conf_missing:
            return self._dead(eb, ap)
        slots: dict[str, SlotPosterior] = {}
        p = ap.intent_candidates or [ap.intent]
        slots["intent"] = SlotPosterior(entity_id=None, top=ap.intent, runner_up=next((i for i in p if i != ap.intent), None),
                                        p_top=1.0 / len(p), p_second=1.0 / len(p) if len(p) > 1 else 0.0)
        top_party: str | None = None
        for slot in ("party", "amount", "date"):  # party first: the amount and date priors are conditioned on it
            eid = ap.slots.get(slot)
            ent = eb.lattice.get(eid) if eid else None
            if ent is None or slot not in self.catalog.critical_slots(ap.intent):
                continue
            ranked = fuse(ent.candidates, self._prior(slot, eb, top_party))
            slots[slot] = SlotPosterior(entity_id=ent.id, top=ranked[0][0], runner_up=ranked[1][0] if len(ranked) > 1 else None,
                                        p_top=ranked[0][1], p_second=ranked[1][1] if len(ranked) > 1 else 0.0)
            top_party = str(ranked[0][0]) if slot == "party" else top_party
        return self._build(eb, ap, slots, ap.intent, ap.intent_agreement, False, 0, {}, eb.dialogue_ctx)

    # ---- stage j+1
    def update(self, outcome: ProbeOutcome) -> ScoredProposal:
        base = outcome.base_sp
        upd = apply(base, outcome, self.probe.eps_c, self.probe.eta, self.probe.kappa, self.probe.gamma)
        ctx = outcome.answer_eb.dialogue_ctx.model_copy(update={"cell": (base.ctx.cell if base.ctx else None) or outcome.spec.cell})
        parts: dict[str, float | None] = {
            "stage_index": float(base.stage_index + 1), "probe_type": float(F.PROBE_TYPE[outcome.spec.kind]), "answer_conf": outcome.answer_conf,
            "answer_agrees": None if upd.agrees is None else float(upd.agrees)}
        intent: Intent = upd.intent or base.ap.intent  # type: ignore[assignment]
        return self._build(base.eb, base.ap, upd.slots, intent, upd.intent_agreement, upd.dup_cleared, base.stage_index + 1, parts, ctx,
                           probe_id=outcome.probe_id)

    # ---- internals
    def _prior(self, slot: str, eb: EvidenceBundle, top_party: str | None) -> Callable[[Value], float]:
        if "prior" in self.ablate:
            return lambda v: 1.0
        ctx = eb.prior_context.get(top_party or "")
        if slot == "party":
            return lambda v: party_prior(eb.prior_context.get(str(v)), eb.clock_ts)
        if slot == "amount":
            return lambda v: amount_prior(int(v), ctx.amount_hist if ctx else [], self.catalog.limits.hard_max_inr)
        return lambda v: date_prior(str(v), eb.today, ctx.last_entry_ts if ctx else None)

    def _prior_top(self, slot: str, eb: EvidenceBundle, sp: SlotPosterior, top_party: str | None) -> float:
        """Prior of the top value, normalised against the runner-up: the prior part of the evidence for the winner."""
        prior = self._prior(slot, eb, top_party)
        a = prior(sp.top)
        b = prior(sp.runner_up) if sp.runner_up is not None else 0.0
        return a / (a + b) if a + b > 0 else 0.5

    def _dead(self, eb: EvidenceBundle, ap: ActionProposal) -> ScoredProposal:
        fv = F.vector({})
        return ScoredProposal(
            trace_id=eb.trace_id, ts=eb.ts, sp_id=det_id("SP", ap.ap_id, 0, "dead"), ap=ap, eb=eb, slots={}, s=1.0, s_raw=1.0, stage_index=0,
            scorer_version=self.scorer_version, feature_vector=fv, feature_digest=digest(fv), baseline_signals={"min_conf_critical": None},
            flags=SpFlags(degraded=True), intent=ap.intent, intent_agreement=False, ctx=eb.dialogue_ctx)

    def _build(self, eb: EvidenceBundle, ap: ActionProposal, slots: dict[str, SlotPosterior], intent: Intent, agreement: bool, dup_cleared: bool,
               stage: int, probe_parts: dict[str, float | None], ctx: DialogueCtx, probe_id: str = "") -> ScoredProposal:
        top_party = str(slots["party"].top) if "party" in slots else None
        parts: dict[str, float | None] = {}
        mins: list[float] = []
        for slot in F.SLOTS:
            sp = slots.get(slot)
            if sp is None:
                continue
            ent = eb.lattice.get(sp.entity_id or "")
            ef = ent.features if ent else EntityFeatures()
            parts |= {f"{slot}_{k}": v for k, v in F.slot_features(sp.p_top, sp.p_second, ef, self._prior_top(slot, eb, sp, top_party)).items()}
            if "prior" in self.ablate:
                parts[f"{slot}_prior_log_odds_top"] = None
            if ef.min_conf is not None:
                mins.append(ef.min_conf)
        naive = 1.0 - math.prod(s.p_top for s in slots.values())
        prof = eb.profile
        parts |= {"naive_risk": naive, "intent_agreement": float(agreement), "parse_source": float(F.PARSE_SOURCE[ap.parse_source]),
                  "evon_latency_bucket": F.latency_bucket(ap.evon.latency_ms if ap.evon else None), "intent_p_top": slots["intent"].p_top,
                  "lang": float(eb.lang == "ta"), "code_mix_ratio": F.code_mix_ratio(eb),
                  "amount_magnitude": F.magnitude_bucket(int(slots["amount"].top) if "amount" in slots else None),
                  "stage_index": 0.0, "probe_type": 0.0, "answer_conf": None, "answer_agrees": None, **probe_parts}
        if "channel" not in self.ablate:
            parts |= {"snr_db": prof.snr_db, "bandwidth_class": float(prof.bandwidth_class == "WB"), "plc_fraction": prof.plc_fraction,
                      "clip_fraction": prof.clip_fraction, "noise_nonstat": prof.noise_nonstat, "rate_proxy": prof.rate_proxy,
                      "cqi": float(int(prof.cqi[1]))}
        fv = F.vector(parts)
        needed = tuple(s for s in F.SLOTS if s in self.catalog.critical_slots(intent))
        missing = [s for s in needed if s not in slots]
        degraded = F.is_degraded(tuple(s for s in needed if s in slots), fv)
        if missing:  # no commit is possible; R2 reprompts before this score is used
            s_raw = s = 1.0
        elif self.bundle is None:
            s_raw = s = min(max(naive, S_FLOOR), 1.0)
        else:
            s_raw, s = self.bundle.score_row(fv)
        return ScoredProposal(
            trace_id=eb.trace_id, ts=eb.ts, sp_id=det_id("SP", ap.ap_id, stage, probe_id, digest(fv)), ap=ap, eb=eb, slots=slots, s=s, s_raw=s_raw,
            stage_index=stage, scorer_version=self.scorer_version, feature_vector=fv, feature_digest=digest(fv),
            baseline_signals={"min_conf_critical": min(mins) if mins else None}, flags=SpFlags(degraded=degraded), intent=intent,
            intent_agreement=agreement, dup_cleared=dup_cleared, ctx=ctx)
