"""C7 act(): the only I/O in the gate. Strictly in this order (P7): audit record durable, then the commit or the spoken reply."""

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from lexi.audit.log import Ack
from lexi.common.catalog import Catalog
from lexi.common.clock import Clock
from lexi.common.hashing import canonical_json, sha256_hex
from lexi.common.ids import det_id
from lexi.common.logging import inc
from lexi.common.types import (
    CommitRequest,
    Decision,
    EscalationTicket,
    ProbeOutcome,
    RegistrySnapshot,
    ScoredProposal,
    TurnInput,
    Value,
)
from lexi.gate.decide import DecideInput, cell_of, decide, eligibility, escalate
from lexi.gate.modes import GateMode, HealthState
from lexi.gate.rules import intent_of

AuditAppend = Callable[[str, dict[str, Any], str], Awaitable[Ack]]
CommitFn = Callable[[CommitRequest], Awaitable[None]]
PlannerFn = Callable[[str, Decision, ScoredProposal], Awaitable[None]]
TicketFn = Callable[[EscalationTicket], Awaitable[None]]


class Gate:
    def __init__(self, catalog: Catalog, mode: GateMode, clock: Clock, *, registry: Callable[[], RegistrySnapshot | None],
                 scorer_version: Callable[[], str], audit_append: AuditAppend, commit: CommitFn, to_planner: PlannerFn, ticket: TicketFn,
                 health: HealthState, lambda_min: Mapping[str, float] | None = None, theta_b: float = 0.5,
                 registry_max_age_s: float = 604800.0) -> None:
        self.catalog, self.mode, self.clock, self.health = catalog, mode, clock, health
        self._registry, self._scorer_version = registry, scorer_version
        self._audit, self._commit, self._to_planner, self._ticket = audit_append, commit, to_planner, ticket
        self.lambda_min, self.theta_b, self.registry_max_age_s = lambda_min or {}, theta_b, registry_max_age_s
        self._last_ack_ms = 0.0

    async def on_scored(self, sp: ScoredProposal, latency_ms: Mapping[str, float] | None = None, probe_outcome: ProbeOutcome | None = None,
                        turn_input: TurnInput | None = None) -> Decision:
        cell = cell_of(sp, self.catalog)
        mode: GateMode = "COLLECT" if turn_input is not None and turn_input.collect else self.mode  # a collection call in a live process (recertification, D12)
        inp = DecideInput(sp=sp, mode=mode, now=self.clock.now(), registry=self._pruned(cell), registry_max_age_s=self.registry_max_age_s,
                          scorer_version_active=self._scorer_version(), audit_healthy=self.health.audit_healthy,
                          ledger_healthy=self.health.ledger_healthy, lambda_min=self.lambda_min.get(cell or ""), theta_b=self.theta_b)
        t0 = time.perf_counter()
        decision = decide(inp, self.catalog)
        decide_ms = (time.perf_counter() - t0) * 1000
        collect = {"el": int(eligibility(inp, self.catalog))} if mode == "COLLECT" else None
        dec_json = decision.model_dump(mode="json")
        if dec_json["probe_request"]:
            dec_json["probe_request"]["base_sp"] = sp.sp_id  # the SP is already in this record
        payload: dict[str, Any] = {
            "call_id": sp.eb.session_id, "turn_id": probe_outcome.answer_eb.turn_id if probe_outcome else sp.eb.turn_id, "stage_index": sp.stage_index,
            "mode": mode, "cell": decision.cell, "sp": sp.model_dump(mode="json"), "decision": dec_json,
            "decide_input": inp.model_dump(mode="json", exclude={"sp"}), "collect": collect, "probe_outcome": _compact(probe_outcome),
            "turn_input": turn_input.model_dump(mode="json") if turn_input else None,
            "latency_ms": {**{k: 0.0 for k in ("c3", "c4", "c5")}, **(latency_ms or {}), "decide": decide_ms, "audit_ack": self._last_ack_ms}}
        ack = await self._append(payload, sp.trace_id)
        if ack is None and decision.kind == "COMMIT" and mode != "COLLECT":
            decision = escalate(inp, self.catalog, "AUDIT_UNAVAILABLE")
        inc(f"decisions_{mode}_{decision.kind}")  # /metrics
        if decision.kind == "COMMIT" and mode != "COLLECT" and ack is not None:
            await self._commit(self._request(sp, decision, ack, inp.now))
            return decision
        await self._to_planner(sp.eb.session_id, decision, sp)
        if decision.kind == "ESCALATE" and mode != "COLLECT":  # COLLECT calls are data generation: no operator to serve
            await self._ticket(self._ticket_for(sp, decision, inp.now))
        return decision

    def _pruned(self, cell: str | None) -> RegistrySnapshot | None:
        """The pointer is read once per decision; the copy fixes lease_expiry as seen and keeps only the decision cell (C6 step 7-8)."""
        reg = None if self.mode == "COLD_START" else self._registry()
        if reg is None:
            return None
        return reg.model_copy(update={"cells": {cell: reg.cells[cell]} if cell in reg.cells else {}})

    async def _append(self, payload: dict[str, Any], trace_id: str) -> Ack | None:
        t0 = time.perf_counter()
        try:
            ack = await self._audit("DECISION", payload, trace_id)
        except Exception:  # AuditError or any I/O failure: no ack
            return None
        self._last_ack_ms = (time.perf_counter() - t0) * 1000
        return ack

    def _request(self, sp: ScoredProposal, d: Decision, ack: Ack, now: float) -> CommitRequest:
        intent, slots = intent_of(sp), {k: sp.slots[k].top for k in ("party", "amount", "date") if k in sp.slots}
        assert intent is not None  # only a decision that passed R1 can commit
        key = sha256_hex(canonical_json([sp.eb.session_id, sp.eb.turn_id, intent, slots]))  # C9 step 2: one action, one entry
        return CommitRequest(
            trace_id=sp.trace_id, ts=now, decision_id=d.decision_id, session_id=sp.eb.session_id, turn_id=sp.eb.turn_id, audit_seq=ack.seq,
            audit_hash=ack.hash, intent=intent, party_id=str(slots["party"]), amount_inr=int(slots["amount"]) if "amount" in slots else None,
            date=str(slots["date"]) if "date" in slots else None, idempotency_key=key)

    def _ticket_for(self, sp: ScoredProposal, d: Decision, now: float) -> EscalationTicket:
        cands: dict[str, list[tuple[Value, float]]] = {}
        for k, s in sp.slots.items():
            cands[k] = [(s.top, s.p_top)] + ([(s.runner_up, s.p_second)] if s.runner_up is not None else [])
        eb = sp.eb
        return EscalationTicket(
            trace_id=sp.trace_id, ts=now, ticket_id=det_id("TKT", d.decision_id), decision_id=d.decision_id, session_id=eb.session_id,
            reason_code=d.reason_code, cell=d.cell, candidates=cands,
            evidence_summary={"text": " ".join(w.w for w in eb.words), "lang": eb.lang, "cqi": eb.profile.cqi, "snr_db": eb.profile.snr_db,
                              "s": sp.s, "intent": intent_of(sp), "stage_index": sp.stage_index})


def _compact(o: ProbeOutcome | None) -> dict[str, Any] | None:
    """The answer in the form C14 needs to score probes (value = what the caller restated)."""
    if o is None:
        return None
    ent = o.answer_eb.lattice.get(o.value_entity_id) if o.value_entity_id else None
    return {"probe_id": o.probe_id, "kind": o.kind, "chosen_index": o.chosen_index, "value_entity_id": o.value_entity_id,
            "answer_conf": o.answer_conf, "latency_ms": o.latency_ms, "spec": o.spec.model_dump(mode="json"),
            "value": ent.candidates[0].value if ent and ent.candidates else None}
