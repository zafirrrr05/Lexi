"""C6 certificate authority: the registry (atomic snapshots, effective thresholds from sentinel state), the lease, and issuance.

Everything that changes the registry runs under one lock and ends in a single reference assignment, so a reader never sees a half-built snapshot."""

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any

from lexi.authority.grid import design
from lexi.authority.issue import IssueReport, certify_cell
from lexi.authority.lease import Lease
from lexi.authority.rescore import Rescore, require_version, rescored
from lexi.authority.store import Store
from lexi.common.catalog import Catalog
from lexi.common.clock import Clock
from lexi.common.types import (
    CalibrationRecord,
    CellEntry,
    CellState,
    Certificate,
    Effective,
    LadderRung,
    RegistrySnapshot,
    SentinelEvent,
)

AuditAppend = Callable[[str, dict[str, Any], str], Awaitable[Any]]
WANTED_RUNG: dict[str, int] = {"HEALTHY": 0, "WATCH": 1, "ALERT": 2}


def effective(state: CellState, ladder: Sequence[LadderRung], hedged: Mapping[str, float]) -> Effective:
    """Threshold a cell may use right now (C6 step 6): rung by sentinel state, HEDGED when that rung is absent, nothing without RG0."""
    rungs = {r.rung: r for r in ladder}
    if state == "REVOKED" or 0 not in rungs:
        return Effective(rung=None, lam=None)
    want = WANTED_RUNG[state]
    if want in rungs:
        return Effective(rung=want, lam=rungs[want].lam, cert_id=rungs[want].cert_id)
    lam = rungs[0].lam * hedged[state]
    if want == 2 and 1 in rungs and rungs[1].lam <= lam:  # escalating must never loosen: the certified rung below is already stricter
        return Effective(rung=1, lam=rungs[1].lam, cert_id=rungs[1].cert_id)
    return Effective(rung=want, lam=lam, hedged=True)


def entry(state: CellState, ladder: list[LadderRung], hedged: Mapping[str, float]) -> CellEntry:
    return CellEntry(state=state, ladder=ladder, effective=effective(state, ladder, hedged))


class Authority:
    def __init__(self, catalog: Catalog, det_cfg: Mapping[str, Any], clock: Clock, store_path: Path, *, audit_append: AuditAppend,
                 publish: Callable[[RegistrySnapshot], None], lease_ttl_s: float = 10.0, caller_model_version: str = "CM-v1") -> None:
        self.catalog, self.cfg, self.clock = catalog, det_cfg, clock
        self.audit_append, self.publish, self.caller_model_version = audit_append, publish, caller_model_version
        self.store = Store(store_path)
        self.lease = Lease(clock, lease_ttl_s)
        self._snap: RegistrySnapshot | None = None
        self._grids: dict[str, dict[int, list[float]]] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Load the last snapshot with an expired lease (autonomy returns with the first heartbeat); none = cold start, probe and escalate only."""
        snap = self.store.last_snapshot()
        if snap is not None:
            snap.lease_expiry = 0.0
            self._snap = snap
            self.publish(snap)

    def read(self) -> RegistrySnapshot | None:
        return self._snap  # one reference read: the pointer of C6 step 7

    def certificates(self, cell: str | None = None) -> list[Certificate]:
        return self.store.certificates(cell)

    def onsets(self) -> dict[str, float]:
        return self.store.onsets()

    def reissue_cells(self) -> list[str]:
        """Cells that need a fresh certificate: the sentinel took them to ALERT or REVOKED (C6 step 1)."""
        return [c for c, e in (self._snap.cells if self._snap else {}).items() if e.state in ("ALERT", "REVOKED")]

    # ---- grids
    def design_grid(self, valid: Sequence[CalibrationRecord], cell: str, rung: int, n_calib: int) -> list[float]:
        alpha = self.catalog.alpha_of(cell, rung)
        return design(valid, alpha, n_calib, self.catalog.n_target(alpha), self.catalog.grid)

    def plan(self, valid: Mapping[str, Sequence[CalibrationRecord]], n_calib: Mapping[str, int]) -> None:
        """Fix every grid from Valid before a single Calib call is collected (10.3)."""
        for cell, n in n_calib.items():
            self._grids[cell] = {r: self.design_grid(valid.get(cell, ()), cell, r, n) for r in range(len(self.catalog.rungs))}

    def grid_start(self, cell: str, rung: int) -> float | None:
        """First point of a planned grid: the lowest lambda the cell can certify at that rung."""
        grid = self._grids.get(cell, {}).get(rung)
        return grid[0] if grid else None

    def lambda_min(self, cell: str) -> float | None:
        """Lowest grid point of the cell: COLLECT probes until a call's score is at or below it, so every lambda is simulated exactly (C7)."""
        starts = [g[0] for g in self._grids.get(cell, {}).values() if g]
        return min(starts) if starts else None

    # ---- issuance
    async def issue(self, release_scorer_version: str, calib: Mapping[str, Sequence[CalibrationRecord]], valid: Mapping[str, Sequence[CalibrationRecord]],
                    rescore: Rescore | None = None, since: Mapping[str, float] | None = None, only: Collection[str] | None = None) -> IssueReport:
        """CERT-v1 for every cell that has Calib records (`only` limits it: a recertification touches the cells that were taken to ALERT, not the healthy
        ones). Records older than a cell's recorded shift onset are never used (C6 step 11)."""
        now = self.clock.now()
        since = self.store.onsets() if since is None else since
        report = IssueReport(scorer_version=release_scorer_version)
        issued: dict[str, list[Certificate]] = {}
        for cell in self.catalog.cells():
            recs = [r for r in calib.get(cell, ()) if r.ts >= since.get(cell, float("-inf"))]
            if not recs or (only is not None and cell not in only):
                continue
            val = list(valid.get(cell, ()))
            if rescore is not None:
                recs, val = rescored(recs, rescore, release_scorer_version), rescored(val, rescore, release_scorer_version)
            else:
                require_version(recs, release_scorer_version)
                require_version(val, release_scorer_version)
            certs, report.cells[cell] = certify_cell(self.catalog, self.cfg, cell, recs, val, self._grids.get(cell, {}),
                                                     scorer_version=release_scorer_version, caller_model_version=self.caller_model_version, now=now)
            if report.cells[cell].status in ("ISSUED", "NO_RUNGS"):
                issued[cell] = certs
                report.certificates += certs
                report.reference[cell] = [r.scores[0] for r in sorted(recs, key=lambda r: (r.ts, r.call_id))]
        async with self._lock:
            old = self._snap
            fresh = old is None or old.scorer_version != release_scorer_version  # a new scorer voids every certificate of the old one
            if not issued and not fresh:
                return report
            cells: dict[str, CellEntry] = {}
            for cell in self.catalog.cells():
                prev = old.cells.get(cell) if old else None
                if cell in issued:
                    cells[cell] = entry("HEALTHY", [LadderRung(rung=c.rung, alpha=c.alpha, lam=c.lam, cert_id=c.cert_id, n=c.n, errors=c.errors,
                                                               p=c.p_value, delta=c.delta) for c in issued[cell]], self.catalog.hedged)
                elif prev is None or fresh:
                    cells[cell] = entry(prev.state if prev else "WATCH", [], self.catalog.hedged)
                else:
                    cells[cell] = prev
            events: list[dict[str, Any]] = [{"event": "CERT_ISSUED", "cell": c.cell, "detail": {"cert_id": c.cert_id, "rung": c.rung, "lam": c.lam, "n": c.n,
                                                                         "errors": c.errors, "p": c.p_value}} for c in report.certificates]
            events.append({"event": "PUBLISH", "cell": None, "detail": {"scorer_version": release_scorer_version, "cells": sorted(issued)}})
            superseded = self.catalog.cells() if fresh else list(issued)
            report.version = await self._swap(cells, release_scorer_version, events, report.certificates, superseded=superseded,
                                              cleared_onsets=list(issued))
        return report

    # ---- sentinel
    async def on_sentinel(self, ev: SentinelEvent) -> None:
        snap = self._snap
        if ev.type == "HEARTBEAT":
            if snap is not None and self.lease.extend(snap):
                await self.audit_append("REGISTRY_EVENT", {"event": "LEASE_RESTORED", "version": snap.version, "cell": None,
                                                           "detail": {"lease_expiry": snap.lease_expiry}}, "")
            return
        if ev.cell is None or ev.to_state is None:
            return
        if ev.to_state == "HEALTHY":
            self.store.clear_onset(ev.cell)
        elif ev.shift_onset_ts is not None:
            self.store.set_onset(ev.cell, ev.shift_onset_ts)  # recertification will ignore everything before this
        async with self._lock:
            snap = self._snap
            prev = snap.cells.get(ev.cell) if snap else None
            if snap is None or prev is None or prev.state == ev.to_state:
                return
            cells = {**snap.cells, ev.cell: entry(ev.to_state, prev.ladder, self.catalog.hedged)}
            detail = {"from": prev.state, "to": ev.to_state, "detector": ev.detector, "value": ev.value, "threshold": ev.threshold,
                      "effective": cells[ev.cell].effective.model_dump()}
            await self._swap(cells, snap.scorer_version, [{"event": "STATE", "cell": ev.cell, "detail": detail}])

    async def check_lease(self) -> None:
        snap = self._snap
        if snap is not None and self.lease.newly_lost(snap):
            await self.audit_append("REGISTRY_EVENT", {"event": "LEASE_LOST", "version": snap.version, "cell": None,
                                                       "detail": {"lease_expiry": snap.lease_expiry}}, "")

    async def run_lease_monitor(self, interval_s: float = 1.0) -> None:
        while True:
            await self.check_lease()
            await self.clock.sleep(interval_s)

    # ---- publication (caller holds the lock)
    async def _swap(self, cells: dict[str, CellEntry], scorer_version: str, events: list[dict[str, Any]], certs: Sequence[Certificate] = (), *,
                    superseded: Sequence[str] = (), cleared_onsets: Sequence[str] = ()) -> int:
        old = self._snap
        snap = RegistrySnapshot(version=(old.version if old else 0) + 1, ts=self.clock.now(), lease_expiry=0.0, scorer_version=scorer_version,
                                catalog_version=self.catalog.version, cells=cells)
        for ev in events:  # audited before it can take effect, like every decision (a failing audit leaves the old registry live)
            await self.audit_append("REGISTRY_EVENT", {**ev, "version": snap.version}, "")
        self.store.save(snap, certs, superseded, cleared_onsets)
        snap.lease_expiry = old.lease_expiry if old else 0.0  # the lease carries over at the instant of the swap, after the awaits above
        self._snap = snap
        self.publish(snap)
        return snap.version
