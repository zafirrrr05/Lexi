"""C6: CERT-v1 walk, grid design, effective thresholds, issuance, lease, restart, recertification after a shift."""

import asyncio
import math
from typing import Any

import numpy as np
import pytest
from tests.helpers import CATALOG, decide_input, make_sp

from lexi.authority import stationarity
from lexi.authority.cert_v1 import Trajectories, certify
from lexi.authority.grid import design
from lexi.authority.registry import Authority, effective
from lexi.common.catalog import load_yaml
from lexi.common.errors import RegistryError
from lexi.common.types import CalibrationRecord, LadderRung, RegistrySnapshot, SentinelEvent
from lexi.gate.decide import decide

CFG = load_yaml("detectors.yaml")
CELL = "hi.Q2.T1"  # STANDARD profile: alpha 0.05 -> n_target 142 / 287 / 576 for RG0 / RG1 / RG2


def rec(s: list[float], el: list[int], y: list[int], *, i: int = 0, cell: str = CELL, scorer: str = "v1", ts: float | None = None,
        feats: list[float] | None = None) -> CalibrationRecord:
    return CalibrationRecord(call_id=f"c{cell}{i}", cell=cell, scores=s, elig=el, labels=y, feature_vectors=[{"f": f} for f in (feats or s)],
                             scorer_version=scorer, split="Calib", scenario="S0", tags=[], ts=float(i) if ts is None else ts)


def population(n: int, seed: int, *, cell: str = CELL, scorer: str = "v1", k: float = 1.0, t0: float = 0.0) -> list[CalibrationRecord]:
    """One stage per call, s ~ Beta(0.5, 8), wrong with probability k x s: a calibrated scorer at k = 1."""
    rng = np.random.default_rng(seed)
    s = rng.beta(0.5, 8, n)
    y = rng.random(n) < np.minimum(1.0, k * s)
    return [rec([float(s[i])], [1], [int(y[i])], i=int(t0) + i, cell=cell, scorer=scorer) for i in range(n)]  # call ids are unique across windows


def calib_window(n: int, seed: int, **kw: Any) -> list[CalibrationRecord]:
    """A Calib window that passes the stationarity check. The check false-refuses about 15% of i.i.d. windows (M1 at 1e3 over ~750 decisions,
    ARL ~5k), so tests draw on until one passes instead of depending on a lucky seed."""
    while not stationarity.check(window := population(n, seed, **kw), kw.get("cell", CELL), CFG)[0]:
        seed += 1000
    return window


def literal_counts(records: list[CalibrationRecord], lam: float) -> tuple[int, int]:
    """10.2 written out call by call, as the oracle for the vectorised code."""
    n = e = 0
    for r in records:
        for j, (s, el) in enumerate(zip(r.scores, r.elig, strict=True)):
            if el == 1 and s <= lam:
                n, e = n + 1, e + r.labels[j]
                break
    return n, e


# ---------------------------------------------------------------------------- CERT-v1 and grid
def test_trajectory_counts_match_the_definition_with_probes_and_ineligible_stages():
    rng = np.random.default_rng(0)
    records = []
    for i in range(300):
        j = int(rng.integers(1, 4))
        records.append(rec(list(rng.random(j)), [int(x) for x in rng.integers(0, 2, j)], [int(x) for x in rng.integers(0, 2, j)], i=i))
    traj = Trajectories(records)
    for lam in (0.05, 0.3, 0.6, 1.0):
        assert traj.counts(lam) == literal_counts(records, lam)


def test_certify_stops_at_the_first_failure_even_if_a_later_point_would_pass():
    records = ([rec([0.04], [1], [0], i=i) for i in range(300)] + [rec([0.09], [1], [1], i=300 + i) for i in range(100)]
               + [rec([0.19], [1], [0], i=400 + i) for i in range(2000)])
    res = certify(records, [0.05, 0.10, 0.20], 0.05, 0.025)
    assert res.lam == 0.05 and (res.n, res.errors) == (300, 0) and len(res.points) == 2  # lambda = 0.2 never tested: fixed sequence
    assert res.p == pytest.approx(0.95**300)


def test_certify_first_point_failing_means_no_rung():
    res = certify([rec([0.01], [1], [1], i=i) for i in range(50)], [0.05, 0.1], 0.05, 0.025)
    assert res.lam is None and res.p is None and len(res.points) == 1


def test_grid_starts_where_expected_commits_first_reach_n_target_and_has_17_points():
    valid = population(4000, 1)
    n_target = CATALOG.n_target(0.05)
    grid = design(valid, 0.05, 2000, n_target, CATALOG.grid)
    assert len(grid) == 17 and all(b / a == pytest.approx(2 ** 0.25) for a, b in zip(grid, grid[1:], strict=False))
    traj = Trajectories(valid)
    assert 2000 * traj.counts(grid[0])[0] / 4000 >= n_target > 2000 * traj.counts(grid[0] / 2 ** 0.25)[0] / 4000
    assert grid[0] < 0.05  # the first point can sit below alpha_r (10.3 "smallest i")


def test_grid_is_capped_at_max_lambda_and_absent_when_the_rung_is_out_of_reach():
    assert max(design(population(4000, 2), 0.2, 6000, 20, CATALOG.grid)) <= CATALOG.grid.max_lambda
    assert design(population(4000, 3), 0.0125, 100, CATALOG.n_target(0.0125), CATALOG.grid) == []  # 100 calls cannot give 576 commits
    assert design([], 0.05, 1000, 142, CATALOG.grid) == []


# ---------------------------------------------------------------------------- effective thresholds
def ladder(*rungs: tuple[int, float]) -> list[LadderRung]:
    return [LadderRung(rung=r, alpha=0.05 / 2**r, lam=lam, cert_id=f"C{r}", n=300, errors=2, p=0.01, delta=0.025) for r, lam in rungs]


HEDGED = CATALOG.hedged


@pytest.mark.parametrize(("state", "lad", "expect"), [
    ("HEALTHY", ladder((0, 0.2), (1, 0.1), (2, 0.05)), (0, 0.2, False)),
    ("WATCH", ladder((0, 0.2), (1, 0.1), (2, 0.05)), (1, 0.1, False)),
    ("ALERT", ladder((0, 0.2), (1, 0.1), (2, 0.05)), (2, 0.05, False)),
    ("WATCH", ladder((0, 0.2)), (1, 0.2 * HEDGED["WATCH"], True)),
    ("ALERT", ladder((0, 0.2)), (2, 0.2 * HEDGED["ALERT"], True)),
    ("ALERT", ladder((0, 0.2), (1, 0.02)), (1, 0.02, False)),  # escalation never loosens: the certified RG1 is stricter than the hedged value
    ("ALERT", ladder((0, 0.2), (1, 0.15)), (2, 0.2 * HEDGED["ALERT"], True)),
    ("REVOKED", ladder((0, 0.2), (1, 0.1)), (None, None, False)),
    ("HEALTHY", ladder((1, 0.1)), (None, None, False)),  # no RG0: no autonomy
    ("WATCH", [], (None, None, False)),
])
def test_effective_threshold_by_state(state, lad, expect):
    eff = effective(state, lad, HEDGED)
    assert (eff.rung, eff.lam, eff.hedged) == pytest.approx(expect) if expect[0] is not None else (eff.rung, eff.lam) == (None, None)
    assert (eff.cert_id is None) == (eff.hedged or eff.rung is None)  # a hedged value carries no certificate


# ---------------------------------------------------------------------------- authority
class Rig:
    def __init__(self, tmp_path, clock) -> None:
        self.audit: list[dict[str, Any]] = []
        self.published: list[RegistrySnapshot] = []
        self.auth = Authority(CATALOG, CFG, clock, tmp_path / "certs.db", audit_append=self._audit, publish=self.published.append)
        self.clock = clock

    async def _audit(self, kind: str, payload: dict[str, Any], trace_id: str) -> None:
        assert kind == "REGISTRY_EVENT"
        self.audit.append(payload)

    def events(self, name: str) -> list[dict[str, Any]]:
        return [a for a in self.audit if a["event"] == name]

    async def beat(self) -> None:
        await self.auth.on_sentinel(SentinelEvent(type="HEARTBEAT"))

    async def change(self, cell: str, to: str, onset: float | None = None) -> None:
        await self.auth.on_sentinel(SentinelEvent(type="STATE_CHANGE", cell=cell, to_state=to, shift_onset_ts=onset))  # type: ignore[arg-type]


async def issued(tmp_path, clock, n: int = 1500, **kw) -> Rig:
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    await rig.auth.issue("v1", {CELL: calib_window(n, 5, **kw)}, {CELL: population(3000, 6, **kw)})
    return rig


async def test_cold_start_has_no_registry(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    assert rig.auth.read() is None and rig.auth.lambda_min(CELL) is None and rig.auth.reissue_cells() == []
    await rig.beat()  # nothing to extend
    assert not rig.audit


async def test_issue_publishes_one_snapshot_with_certificates_and_audit(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    rep = await rig.auth.issue("v1", {CELL: calib_window(1500, 5)}, {CELL: population(3000, 6)})
    snap = rig.auth.read()
    assert snap is not None and rep.version == snap.version == 1 and rig.published == [snap]
    assert rep.cells[CELL].status == "ISSUED" and [r.rung for r in snap.cells[CELL].ladder] == [c.rung for c in rep.certificates]
    assert snap.cells[CELL].state == "HEALTHY" and snap.cells[CELL].effective.rung == 0
    assert set(snap.cells) == set(CATALOG.cells()) and snap.cells["ta.Q1.T2"].effective.lam is None  # other cells: no certificate, no autonomy
    assert [a["event"] for a in rig.audit] == ["CERT_ISSUED"] * len(rep.certificates) + ["PUBLISH"] and {a["version"] for a in rig.audit} == {1}
    stored = rig.auth.certificates(CELL)
    assert [c.cert_id for c in stored] == [c.cert_id for c in rep.certificates] and all(c.state == "ACTIVE" for c in stored)
    assert math.isclose(stored[0].alpha, 0.05) and stored[0].calib_window["hash"] and stored[0].caller_model_version == "CM-v1"
    assert rep.reference[CELL] and len(rep.reference[CELL]) == 1500


async def test_fewer_records_than_n_target_never_get_a_rung(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    few = [rec([0.001], [1], [0], i=i) for i in range(CATALOG.n_target(0.05) - 1)]  # all correct, all commit: the p-value alone would pass
    rep = await rig.auth.issue("v1", {CELL: few}, {CELL: few})
    assert rep.cells[CELL].status == "INSUFFICIENT" and not rep.certificates
    assert rig.auth.read() is not None and rig.auth.read().cells[CELL].ladder == []  # type: ignore[union-attr]  # first issue still pairs the scorer
    mid = [rec([0.001], [1], [0], i=i) for i in range(CATALOG.n_target(0.025) - 1)]  # enough for RG0, not for RG1
    rep = await rig.auth.issue("v1", {CELL: mid}, {CELL: mid})
    assert [c.rung for c in rep.certificates] == [0] and rep.cells[CELL].rungs[1].note == "fewer Calib records than n_target"


async def test_a_nonstationary_calib_window_is_refused(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    drift = [rec([0.001 + (0.4 if i >= 400 else 0.0) * (i % 7) / 7], [1], [0], i=i) for i in range(800)]  # scores jump in the second half
    rep = await rig.auth.issue("v1", {CELL: drift}, {CELL: drift})
    assert rep.cells[CELL].status == "NONSTATIONARY" and (rep.cells[CELL].peak_srm1 or 0) >= CFG["stationarity_threshold"] and not rep.certificates


async def test_heartbeat_extends_the_lease_in_place_without_a_new_version_or_audit(tmp_path, clock):
    rig = await issued(tmp_path, clock)
    snap, n_audit = rig.auth.read(), len(rig.audit)
    assert snap is not None and snap.lease_expiry == 0.0  # nothing is authorised until the sentinel speaks
    await rig.beat()
    assert rig.auth.read() is snap and snap.lease_expiry == clock.now() + 10.0
    assert [a["event"] for a in rig.audit[n_audit:]] == ["LEASE_RESTORED"] and snap.version == 1
    clock.advance(2)
    await rig.beat()
    assert snap.lease_expiry == clock.now() + 10.0 and len(rig.events("LEASE_RESTORED")) == 1 and snap.version == 1


async def test_lease_loss_is_audited_once_and_restored_by_the_next_heartbeat(tmp_path, clock):
    rig = await issued(tmp_path, clock)
    await rig.beat()
    await rig.auth.check_lease()
    assert not rig.events("LEASE_LOST")
    clock.advance(10.5)
    await rig.auth.check_lease()
    await rig.auth.check_lease()
    assert len(rig.events("LEASE_LOST")) == 1
    await rig.beat()
    assert len(rig.events("LEASE_RESTORED")) == 2


async def test_state_change_republishes_the_effective_threshold_and_keeps_the_lease(tmp_path, clock):
    rig = await issued(tmp_path, clock)
    await rig.beat()
    before = rig.auth.read()
    assert before is not None
    await rig.change(CELL, "WATCH", onset=12.0)
    after = rig.auth.read()
    assert after is not None and after.version == before.version + 1 and after.lease_expiry == before.lease_expiry
    eff = after.cells[CELL].effective
    assert after.cells[CELL].state == "WATCH" and eff.lam is not None and (eff.hedged or eff.rung == 1)
    assert before.cells[CELL].effective.rung == 0  # the old snapshot object is untouched
    assert rig.events("STATE")[-1]["detail"]["to"] == "WATCH" and rig.auth.onsets() == {CELL: 12.0}
    await rig.change(CELL, "WATCH")  # same state again: nothing to publish
    assert rig.auth.read() is after
    await rig.change(CELL, "REVOKED", onset=11.0)
    assert rig.auth.read().cells[CELL].effective.lam is None and rig.auth.onsets() == {CELL: 11.0} and rig.auth.reissue_cells() == [CELL]  # type: ignore[union-attr]
    await rig.change(CELL, "HEALTHY")
    assert rig.auth.onsets() == {} and rig.auth.read().cells[CELL].effective.rung == 0  # type: ignore[union-attr]


async def test_restart_reloads_the_last_snapshot_with_an_expired_lease(tmp_path, clock):
    rig = await issued(tmp_path, clock)
    await rig.beat()
    await rig.change(CELL, "ALERT", onset=5.0)
    last = rig.auth.read()
    again = Rig(tmp_path, clock)
    await again.auth.start()
    snap = again.auth.read()
    assert snap is not None and last is not None and snap.version == last.version and snap.lease_expiry == 0.0
    assert snap.cells[CELL].state == "ALERT" and again.published == [snap] and again.auth.onsets() == {CELL: 5.0}
    await again.beat()
    assert snap.lease_expiry == clock.now() + 10.0 and again.events("LEASE_RESTORED")


async def test_recertification_after_a_shift_uses_only_records_from_the_onset(tmp_path, clock):
    rig = await issued(tmp_path, clock, n=1500)
    await rig.change(CELL, "REVOKED", onset=1000.0)
    old, new = population(1500, 7, k=3.0, t0=0.0), calib_window(1500, 8, t0=1000.0)  # before the onset the world was bad, after it good again
    clock.advance(60)
    rep = await rig.auth.issue("v1", {CELL: old[:1000] + new}, {CELL: population(3000, 9)})
    assert rep.cells[CELL].n_records == 1500 and rep.cells[CELL].status == "ISSUED"  # the 1000 records with ts < onset were dropped
    snap = rig.auth.read()
    assert snap is not None and snap.cells[CELL].state == "HEALTHY" and rig.auth.onsets() == {}
    assert all(c.state == "ACTIVE" for c in rig.auth.certificates(CELL)[-len(rep.certificates):])
    assert {c.state for c in rig.auth.certificates(CELL)[:-len(rep.certificates)]} == {"SUPERSEDED"}


async def test_failed_recertification_keeps_the_old_ladder_and_state(tmp_path, clock):
    rig = await issued(tmp_path, clock)
    await rig.change(CELL, "REVOKED", onset=0.0)
    rep = await rig.auth.issue("v1", {CELL: population(50, 3)}, {CELL: population(500, 4)})  # too few records
    snap = rig.auth.read()
    assert rep.version is None and snap is not None and snap.cells[CELL].state == "REVOKED" and snap.cells[CELL].ladder


async def test_new_scorer_voids_old_certificates_and_is_rescored_from_feature_vectors(tmp_path, clock):
    rig = await issued(tmp_path, clock)
    seen: list[int] = []

    def rescore(rows):
        seen.append(len(rows))
        return [min(1.0, r["f"] * 0.5) for r in rows]  # the staged scorer is more optimistic than the recorded one

    calib, valid = calib_window(1500, 5, scorer="v1"), population(3000, 6, scorer="v1")
    rep = await rig.auth.issue("v2", {CELL: calib}, {CELL: valid}, rescore=rescore)
    snap = rig.auth.read()
    assert snap is not None and snap.scorer_version == "v2" and seen == [1500, 3000] and rep.cells[CELL].status == "ISSUED"
    assert [r.lam for r in snap.cells[CELL].ladder][0] < 0.5
    other = await rig.auth.issue("v3", {}, {})  # a scorer with no certificates anywhere: every cell loses its ladder
    assert other.version == snap.version + 1 and all(not e.ladder for e in rig.auth.read().cells.values())  # type: ignore[union-attr]


async def test_records_from_another_scorer_are_a_mismatch(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    with pytest.raises(RegistryError):
        await rig.auth.issue("v2", {CELL: population(300, 1, scorer="v1")}, {CELL: population(300, 2, scorer="v2")})


async def test_plan_fixes_grids_before_calib_and_lambda_min_is_their_lowest_start(tmp_path, clock):
    rig = Rig(tmp_path, clock)
    await rig.auth.start()
    valid = {CELL: population(3000, 11)}
    rig.auth.plan(valid, {CELL: 2000})
    grids = {r: rig.auth.design_grid(valid[CELL], CELL, r, 2000) for r in range(3)}
    assert rig.auth.lambda_min(CELL) == min(g[0] for g in grids.values() if g) and rig.auth.lambda_min("ta.Q1.T2") is None
    rep = await rig.auth.issue("v1", {CELL: calib_window(2000, 12)}, valid)
    assert [r.grid for r in rep.cells[CELL].rungs] == [grids[r] for r in range(3)]  # issuance used the planned grids


async def test_lease_expiry_means_no_autonomy_in_the_gate(tmp_path, clock):
    """AV2 in miniature: the same score commits while the lease is live and escalates the instant it lapses."""
    rig = await issued(tmp_path, clock)
    await rig.beat()
    snap = rig.auth.read()
    assert snap is not None
    sp = make_sp("sharma ji ka do hazaar jama kar do", conf=0.99)
    lam = snap.cells[CELL].effective.lam
    assert lam is not None
    sp = sp.model_copy(update={"s": lam / 2, "scorer_version": "v1"})
    di = decide_input(sp, reg=snap, now=snap.lease_expiry - 0.1)
    live = decide(di.model_copy(update={"scorer_version_active": "v1"}), CATALOG)
    lapsed = decide(di.model_copy(update={"scorer_version_active": "v1", "now": snap.lease_expiry + 0.1}), CATALOG)
    assert live.kind in ("COMMIT", "PROBE") and (lapsed.kind, lapsed.reason_code) != ("COMMIT", "CERTIFIED")
    assert lapsed.kind == "ESCALATE" and lapsed.reason_code == "NO_AUTONOMY"


async def test_readers_never_see_a_torn_snapshot_while_the_registry_changes(tmp_path, clock):
    """L5: a reader thread polls the pointer while states flip; every snapshot it sees must be internally consistent."""
    rig = await issued(tmp_path, clock)
    stop, seen, bad = False, [], []

    def reader() -> None:
        while not stop:
            snap = rig.auth.read()
            if snap is not None:
                seen.append(snap.version)
                for cell, e in snap.cells.items():
                    if e.effective != effective(e.state, e.ladder, HEDGED):
                        bad.append((snap.version, cell))

    thread = asyncio.get_running_loop().run_in_executor(None, reader)
    for i in range(150):
        await rig.change(CELL, "WATCH" if i % 2 == 0 else "HEALTHY")
        await asyncio.sleep(0)
    stop = True
    await thread
    assert not bad and seen == sorted(seen) and len(set(seen)) > 1
