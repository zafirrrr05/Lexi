"""C7 act(): audit before act, fail closed without an ack, no commit in COLLECT, replay of a recorded decision."""

from typing import Any

from tests.helpers import CATALOG, OK_TEXT, make_sp, registry

from lexi.audit.log import Ack
from lexi.common.clock import ManualClock
from lexi.common.errors import AuditError
from lexi.common.types import CommitRequest, Decision, EscalationTicket, ScoredProposal
from lexi.gate.act import Gate
from lexi.gate.decide import DecideInput, decide
from lexi.gate.modes import HealthState


class Rig:
    """Records the order of every side effect so audit-before-act is checkable."""

    def __init__(self, mode: str = "LIVE", reg: Any = "default", fail_audit: bool = False, lam: float = 0.9) -> None:
        self.events: list[str] = []
        self.records: list[dict[str, Any]] = []
        self.commits: list[CommitRequest] = []
        self.planned: list[Decision] = []
        self.tickets: list[EscalationTicket] = []
        self.fail_audit = fail_audit
        reg_obj = registry(lam=lam, lease=1e12) if reg == "default" else reg
        self.gate = Gate(CATALOG, mode, ManualClock(10.0), registry=lambda: reg_obj, scorer_version=lambda: "bootstrap-naive",  # type: ignore[arg-type]
                         audit_append=self.audit, commit=self.commit, to_planner=self.plan, ticket=self.ticket, health=HealthState(),
                         lambda_min={"hi.Q2.T1": 0.01})

    async def audit(self, kind: str, payload: dict[str, Any], trace: str) -> Ack:
        if self.fail_audit:
            raise AuditError("disk full")
        self.events.append("audit")
        self.records.append(payload)
        return Ack(seq=len(self.records), hash=f"h{len(self.records)}")

    async def commit(self, req: CommitRequest) -> None:
        self.events.append("commit")
        self.commits.append(req)

    async def plan(self, session: str, d: Decision, sp: ScoredProposal) -> None:
        self.events.append("plan")
        self.planned.append(d)

    async def ticket(self, t: EscalationTicket) -> None:
        self.events.append("ticket")
        self.tickets.append(t)


async def test_commit_is_sent_only_after_the_audit_ack_and_carries_it() -> None:
    rig = Rig()
    d = await rig.gate.on_scored(make_sp(OK_TEXT))
    assert d.kind == "COMMIT" and rig.events == ["audit", "commit"]
    req = rig.commits[0]
    assert (req.audit_seq, req.audit_hash, req.party_id, req.amount_inr, req.intent) == (1, "h1", "P01", 2000, "CREDIT_ENTRY")
    assert req.decision_id == d.decision_id and len(req.idempotency_key) == 64
    again = await Rig().gate.on_scored(make_sp(OK_TEXT))
    assert again.decision_id == d.decision_id  # same inputs, same key: duplicate delivery returns the original receipt (C9)


async def test_no_ack_turns_commit_into_escalate() -> None:
    rig = Rig(fail_audit=True)
    d = await rig.gate.on_scored(make_sp(OK_TEXT))
    assert (d.kind, d.reason_code) == ("ESCALATE", "AUDIT_UNAVAILABLE")
    assert rig.commits == [] and rig.events == ["plan", "ticket"]


async def test_escalate_sends_ticket_with_candidates_and_planner_gets_every_other_kind() -> None:
    rig = Rig(reg=None)
    d = await rig.gate.on_scored(make_sp(OK_TEXT))
    assert d.reason_code == "NO_AUTONOMY" and rig.events == ["audit", "plan", "ticket"]
    t = rig.tickets[0]
    assert t.session_id == "s1" and t.candidates["amount"][0] == (2000, t.candidates["amount"][0][1]) and t.evidence_summary["text"] == OK_TEXT


async def test_collect_never_commits_and_records_eligibility() -> None:
    rig = Rig(mode="COLLECT")
    d = await rig.gate.on_scored(make_sp(OK_TEXT))
    assert d.kind == "PROBE" and rig.commits == [] and rig.records[0]["collect"] == {"el": 1} and rig.records[0]["mode"] == "COLLECT"
    over = await Rig(mode="COLLECT").gate.on_scored(make_sp("sharma ji ka pachas hazaar jama kar do", conf=0.99))
    assert over.kind == "ESCALATE"


async def test_collect_escalation_raises_no_operator_ticket() -> None:
    rig = Rig(mode="COLLECT")
    await rig.gate.on_scored(make_sp("sharma ji ka pachas hazaar jama kar do", conf=0.99))
    assert rig.tickets == []


async def test_cold_start_ignores_the_registry() -> None:
    rig = Rig(mode="COLD_START")
    d = await rig.gate.on_scored(make_sp(OK_TEXT))
    assert d.reason_code == "NO_AUTONOMY" and d.registry_version is None and rig.commits == []


async def test_audit_payload_has_the_contract_keys_and_replays_bit_identically() -> None:
    rig = Rig(lam=0.01)  # forces a PROBE, so the decision carries a probe request
    d = await rig.gate.on_scored(make_sp(OK_TEXT), latency_ms={"c3": 1.0})
    rec = rig.records[0]
    assert set(rec) == {"call_id", "turn_id", "stage_index", "mode", "cell", "sp", "decision", "decide_input", "collect", "probe_outcome", "turn_input",
                        "latency_ms"}
    assert set(rec["latency_ms"]) == {"c3", "c4", "c5", "decide", "audit_ack"} and rec["decision"]["probe_request"]["base_sp"] == rec["sp"]["sp_id"]
    inp = DecideInput(sp=ScoredProposal.model_validate(rec["sp"]), **rec["decide_input"])
    replay = decide(inp, CATALOG).model_dump(mode="json")
    replay["probe_request"]["base_sp"] = rec["sp"]["sp_id"]
    assert replay == rec["decision"] and d.kind == "PROBE"


async def test_registry_is_pruned_to_the_decision_cell_and_lease_is_recorded_as_seen() -> None:
    reg = registry(lam=0.9, lease=1e12)
    reg.cells["ta.Q1.T2"] = reg.cells["hi.Q2.T1"]
    rig = Rig(reg=reg)
    await rig.gate.on_scored(make_sp(OK_TEXT))
    seen = rig.records[0]["decide_input"]["registry"]
    assert list(seen["cells"]) == ["hi.Q2.T1"] and seen["lease_expiry"] == 1e12
