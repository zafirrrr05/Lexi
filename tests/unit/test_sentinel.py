"""C10 state machine and service: escalation, clearing, staleness, certificates, audit/outcome feeds, heartbeat, checkpoint restart."""

import asyncio
from typing import Any

import pytest
from tests.helpers import CATALOG

from lexi.common.catalog import load_yaml
from lexi.common.types import (
    AuditRecord,
    CanaryRequest,
    CellEntry,
    Effective,
    LabeledOutcome,
    LadderRung,
    RegistrySnapshot,
    SentinelEvent,
)
from lexi.sentinel import checkpoint
from lexi.sentinel.machine import CellMachine, Sentinel

CFG = load_yaml("detectors.yaml")
CELL = "hi.Q2.T1"
REF = [i / 500 for i in range(500)]
LOW, HIGH = -1.0, 2.0  # below / above every reference score: p ~ 1 (calm) / p ~ 0 (shifted)


def machine(state: str = "HEALTHY") -> CellMachine:
    m = CellMachine(CELL, CATALOG.alpha_of(CELL, 0), CFG)
    m.m1.reset(REF)
    m.on_certificate(("C-1",), 0.0)
    m.state = state  # type: ignore[assignment]
    return m


def test_shift_escalates_monotonically_and_reports_onset():
    m = machine()
    for t in range(5):
        assert m.on_score(LOW, float(t), float(t)) is None
    seen = []
    for t in range(100, 110):
        if (tr := m.on_score(HIGH, float(t), float(t))) is not None:
            seen.append(tr)
    assert seen and seen[-1].dst == "REVOKED"
    assert all(a.dst != b.dst and ["HEALTHY", "WATCH", "ALERT", "REVOKED"].index(b.dst) > ["HEALTHY", "WATCH", "ALERT", "REVOKED"].index(a.dst)
               for a, b in zip(seen, seen[1:], strict=False))
    assert seen[0].onset == 100.0 and seen[0].detector == "M1"
    assert m.on_score(LOW, 200.0, 200.0) is None and m.state == "REVOKED"  # nothing clears REVOKED but a certificate


def test_reference_freezes_when_not_healthy():
    m = machine()
    m.on_score(LOW, 0.0, 0.0)
    assert len(m.m1.ref) == 501
    m.state = "WATCH"
    m.on_score(LOW, 1.0, 1.0)
    assert len(m.m1.ref) == 501


def test_watch_clears_after_200_calm_decisions_m1_only():
    m = machine("WATCH")
    moves = [m.on_score(LOW, float(t), float(t)) for t in range(250)]
    assert [i for i, t in enumerate(moves) if t] == [199]
    assert m.state == "HEALTHY" and moves[199].detector == "M1"  # type: ignore[union-attr]


def test_a_spike_restarts_the_calm_count():
    m = machine("WATCH")
    for t in range(150):
        m.on_score(LOW, float(t), float(t))
    m.m1.sr = 100.0
    m.on_score(LOW, 150.0, 150.0)  # (1 + 100) x ~0.5 stays above 20
    assert m.calm == 0 and m.state == "WATCH"


def test_alert_and_revoked_never_clear_from_scores():
    for state in ("ALERT", "REVOKED"):
        m = machine(state)
        assert all(m.on_score(LOW, float(t), float(t)) is None for t in range(400))
        assert m.state == state


def test_m2_alert_then_revoke_from_wrong_commits():
    m = machine()
    states = [t.dst for t in (m.on_commit_outcome(1, float(i)) for i in range(12)) if t]
    assert states == ["ALERT", "REVOKED"] or states == ["REVOKED"]
    assert m.state == "REVOKED"


def test_m2_has_no_watch_tier():
    m = machine()
    m.m2.sr = 5e3
    m.m2.update(0)
    assert m.state == "HEALTHY"


def test_staleness_needs_both_commits_and_silence():
    m = machine()
    for _ in range(49):
        m.on_autonomous_commit()
    assert m.check_stale(10_000.0) is None  # 49 commits: not enough
    m.on_autonomous_commit()
    assert m.check_stale(1799.0) is None  # 30 minutes not reached
    t = m.check_stale(1800.0)
    assert t and (t.dst, t.detector) == ("WATCH", "STALENESS")


def test_stale_cell_does_not_clear_until_a_label_arrives():
    m = machine()
    for _ in range(50):
        m.on_autonomous_commit()
    m.check_stale(1800.0)
    assert m.state == "WATCH"
    assert all(m.on_score(LOW, float(t), 2000.0) is None for t in range(300))
    m.on_gold_label(2000.0)
    assert m.on_score(LOW, 301.0, 2000.0) is not None and m.state == "HEALTHY"


def test_new_certificate_resets_detectors_and_state():
    m = machine("REVOKED")
    m.m1.sr = m.m2.sr = 1e6
    t = m.on_certificate(("C-2",), 50.0)
    assert t and (t.src, t.dst, t.detector) == ("REVOKED", "HEALTHY", None)
    assert m.m1.sr == m.m2.sr == 0.0 and len(m.m1.ref) == 500 and not m.pending_cert


def test_dump_load_continues_identically():
    a = machine()
    for t in range(40):
        a.on_score(REF[(t * 37) % 500], float(t), float(t))
    b = CellMachine(CELL, CATALOG.alpha_of(CELL, 0), CFG)
    b.load(a.dump())
    for t in range(40, 80):
        assert a.on_score(REF[(t * 11) % 500], float(t), float(t)) == b.on_score(REF[(t * 11) % 500], float(t), float(t))
    assert a.m1.sr == b.m1.sr


# ---------------------------------------------------------------------------- service
class Rig:
    def __init__(self, tmp_path, clock) -> None:
        self.events: list[SentinelEvent] = []
        self.audit: list[tuple[str, dict[str, Any]]] = []
        self.canaries: list[CanaryRequest] = []
        self.sentinel = Sentinel(CATALOG, CFG, clock, tmp_path, emit=self._emit, audit_append=self._audit, canary=self._canary)

    async def _emit(self, ev: SentinelEvent) -> None:
        self.events.append(ev)

    async def _audit(self, kind: str, payload: dict[str, Any], trace_id: str) -> None:
        self.audit.append((kind, payload))

    async def _canary(self, req: CanaryRequest) -> None:
        self.canaries.append(req)

    def changes(self) -> list[SentinelEvent]:
        return [e for e in self.events if e.type == "STATE_CHANGE"]


def snap(cert: str, cells: tuple[str, ...] = (CELL,)) -> RegistrySnapshot:
    rung = LadderRung(rung=0, alpha=0.02, lam=0.2, cert_id=cert, n=400, errors=3, p=0.01, delta=0.025)
    return RegistrySnapshot(version=1, lease_expiry=1e12, scorer_version="s", catalog_version=CATALOG.version,
                            cells={c: CellEntry(state="HEALTHY", ladder=[rung], effective=Effective(rung=0, lam=0.2, cert_id=cert)) for c in cells})


def decision(s: float, *, stage: int = 0, mode: str = "LIVE", kind: str = "PROBE", cell: str | None = CELL, ts: float = 1.0) -> AuditRecord:
    return AuditRecord(seq=1, prev_hash="", hash="", type="DECISION", ts=ts,
                       payload={"cell": cell, "mode": mode, "stage_index": stage, "sp": {"s": s}, "decision": {"kind": kind}})


def outcome(e: int, *, tier: str = "G", kind: str = "COMMIT", autonomous: bool = True, ts: float = 1.0) -> LabeledOutcome:
    return LabeledOutcome(call_id="c", cell=CELL, decision_kind=kind, committed_values={}, y_final=e, stage_index=0,  # type: ignore[arg-type]
                          score_trajectory=[0.1], label_tier=tier, autonomous=autonomous, ts=ts)  # type: ignore[arg-type]


async def certified(tmp_path, clock) -> Rig:
    rig = Rig(tmp_path, clock)
    await rig.sentinel.start()
    rig.sentinel.set_reference(CELL, REF)
    await rig.sentinel.on_registry(snap("C-1"))
    return rig


async def test_cold_start_is_watch_until_the_first_certificate(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.sentinel.start()
    assert {v["state"] for v in rig.sentinel.states().values()} == {"WATCH"}
    rig.sentinel.set_reference(CELL, REF)
    await rig.sentinel.on_registry(snap("C-1"))
    st = rig.sentinel.states()
    assert st[CELL]["state"] == "HEALTHY" and st["ta.Q1.T2"]["state"] == "WATCH"
    ev = rig.changes()[0]
    assert (ev.cell, ev.from_state, ev.to_state, ev.detector) == (CELL, "WATCH", "HEALTHY", None)
    assert rig.audit[0][0] == "SENTINEL_EVENT" and not rig.canaries


async def test_baseline_snapshot_is_not_a_new_certificate(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.sentinel.start(snap("C-1"))
    assert rig.sentinel.states()[CELL]["state"] == "WATCH"
    await rig.sentinel.on_registry(snap("C-1"))
    assert rig.sentinel.states()[CELL]["state"] == "WATCH"
    await rig.sentinel.on_registry(snap("C-2"))
    assert rig.sentinel.states()[CELL]["state"] == "HEALTHY"


async def test_shift_in_the_audit_stream_raises_alert_with_audit_event_and_canary(tmp_path, clock):
    rig = await certified(tmp_path, clock)
    for t in range(50):
        await rig.sentinel.on_audit(decision(LOW, ts=float(t)))
    for t in range(100, 106):
        await rig.sentinel.on_audit(decision(HIGH, ts=float(t)))
    last = rig.changes()[-1]
    assert last.to_state == "REVOKED" and last.shift_onset_ts == 100.0 and last.cell == CELL
    kinds = [k for k, _ in rig.audit]
    assert kinds.count("SENTINEL_EVENT") == len(rig.changes())
    assert rig.canaries and rig.canaries[0].cell == CELL and rig.canaries[0].rate_per_min == CFG["canary"]["rate_per_min"]


async def test_only_live_stage_zero_decisions_feed_m1(tmp_path, clock):
    rig = await certified(tmp_path, clock)
    for rec in (decision(HIGH, mode="COLLECT"), decision(HIGH, mode="COLD_START"), decision(HIGH, stage=1), decision(HIGH, cell=None)):
        await rig.sentinel.on_audit(rec)
    assert rig.sentinel.states()[CELL]["n_m1"] == 0
    await rig.sentinel.on_audit(decision(LOW))
    assert rig.sentinel.states()[CELL]["n_m1"] == 1


async def test_outcomes_feed_m2_for_autonomous_gold_commits_only(tmp_path, clock):
    rig = await certified(tmp_path, clock)
    for o in (outcome(1, tier="R"), outcome(1, autonomous=False), outcome(1, kind="ESCALATE")):
        await rig.sentinel.on_outcome(o)
    assert rig.sentinel.states()[CELL]["n_m2"] == 0
    for i in range(12):
        await rig.sentinel.on_outcome(outcome(1, tier="A", ts=float(i)))
    assert rig.changes()[-1].to_state == "REVOKED" and rig.changes()[-1].detector == "M2"


async def test_staleness_runs_on_the_heartbeat(tick, tmp_path):
    rig = Rig(tmp_path, tick)
    await rig.sentinel.start()
    rig.sentinel.set_reference(CELL, REF)
    await rig.sentinel.on_registry(snap("C-1"))
    for _ in range(50):
        await rig.sentinel.on_audit(decision(LOW, kind="COMMIT"))
    task = asyncio.create_task(rig.sentinel.run_heartbeat())
    await tick.advance_async(0)
    assert [e.type for e in rig.events].count("HEARTBEAT") == 1
    await tick.advance_async(1800)
    task.cancel()
    assert [e.type for e in rig.events].count("HEARTBEAT") >= 2
    stale = [e for e in rig.changes() if e.detector == "STALENESS"]
    assert stale and stale[0].cell == CELL and stale[0].to_state == "WATCH"


async def test_restart_restores_state_and_detector_continues_identically(tmp_path, clock):
    rig = await certified(tmp_path, clock)
    stream = [REF[(t * 37) % 500] for t in range(130)]
    for t, s in enumerate(stream):
        await rig.sentinel.on_audit(decision(s, ts=float(t)))
    twin = Rig(tmp_path / "twin", clock)  # an uninterrupted copy fed the same stream, for comparison
    await twin.sentinel.start()
    twin.sentinel.set_reference(CELL, REF)
    await twin.sentinel.on_registry(snap("C-1"))
    for t, s in enumerate(stream):
        await twin.sentinel.on_audit(decision(s, ts=float(t)))

    restarted = Rig(tmp_path, clock)  # checkpoint written at event 100: the last 30 events are replayed from the audit
    await restarted.sentinel.start(snap("C-1"))
    assert restarted.sentinel.states()[CELL]["state"] == "HEALTHY" and restarted.sentinel.states()[CELL]["n_m1"] == 100
    for t, s in list(enumerate(stream))[100:]:
        await restarted.sentinel.on_audit(decision(s, ts=float(t)))
    assert restarted.sentinel.states()[CELL]["srm1"] == pytest.approx(twin.sentinel.states()[CELL]["srm1"])


async def test_alert_survives_restart_even_between_periodic_checkpoints(tmp_path, clock):
    rig = await certified(tmp_path, clock)
    for t in range(6):
        await rig.sentinel.on_audit(decision(HIGH, ts=float(t)))
    assert rig.sentinel.states()[CELL]["state"] in ("ALERT", "REVOKED")
    restarted = Rig(tmp_path, clock)
    await restarted.sentinel.start(snap("C-1"))
    assert restarted.sentinel.states()[CELL]["state"] == rig.sentinel.states()[CELL]["state"]


async def test_recertification_while_down_resets_the_cell(tmp_path, clock):
    rig = await certified(tmp_path, clock)
    for t in range(6):
        await rig.sentinel.on_audit(decision(HIGH, ts=float(t)))
    restarted = Rig(tmp_path, clock)
    await restarted.sentinel.start(snap("C-9"))
    assert restarted.sentinel.states()[CELL]["state"] == "HEALTHY"


async def test_missing_or_foreign_checkpoint_means_watch(tmp_path, clock):
    await certified(tmp_path, clock)
    state = checkpoint.load(tmp_path)
    assert state is not None
    checkpoint.save(tmp_path, {**state, "catalog_version": "other"})
    foreign = Rig(tmp_path, clock)
    await foreign.sentinel.start(snap("C-1"))
    assert foreign.sentinel.states()[CELL]["state"] == "WATCH"
    (tmp_path / checkpoint.FILE).write_text("{ torn")
    torn = Rig(tmp_path, clock)
    await torn.sentinel.start()
    assert {v["state"] for v in torn.sentinel.states().values()} == {"WATCH"}
