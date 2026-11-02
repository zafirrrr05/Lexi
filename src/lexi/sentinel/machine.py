"""C10 drift sentinel: per-cell state machine over detectors M1 and M2, and the service that feeds it from the audit and label streams.

CellMachine is pure (time and events come in, transitions come out). Sentinel does the I/O: audit, events to C6, canaries, checkpoints."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from lexi.common.catalog import Catalog
from lexi.common.clock import Clock
from lexi.common.types import (
    AuditRecord,
    CanaryRequest,
    CellState,
    LabeledOutcome,
    RegistrySnapshot,
    SentinelEvent,
)
from lexi.sentinel import checkpoint, heartbeat
from lexi.sentinel.m1 import M1
from lexi.sentinel.m2 import M2

RANK: dict[str, int] = {"HEALTHY": 0, "WATCH": 1, "ALERT": 2, "REVOKED": 3}
AuditAppend = Callable[[str, dict[str, Any], str], Awaitable[Any]]


class Transition(NamedTuple):
    src: CellState
    dst: CellState
    detector: str | None  # M1 | M2 | STALENESS; None = a new certificate reset the cell
    value: float | None
    threshold: float | None
    onset: float | None


class CellMachine:
    def __init__(self, cell: str, alpha0: float, cfg: Mapping[str, Any]) -> None:
        self.cfg = cfg
        self.m1 = M1(cfg["m1"]["eps_grid"], f"{cfg['seed']}:{cell}")
        self.m2 = M2(alpha0, cfg["m2"]["nu_grid"])
        self.state: CellState = "WATCH"  # until a certificate says otherwise (C10 FAILS)
        self.pending_cert = True  # a WATCH from start-up without a checkpoint clears only by recertification
        self.cert: tuple[str, ...] | None = None
        self.calm = 0  # consecutive decisions with SRM1 below the clearing level
        self.n1 = self.n2 = 0
        self.cusum: dict[str, tuple[float, float | None]] = {"M1": (0.0, None), "M2": (0.0, None)}  # (W, start of the current excursion)
        self.commits_since_label = 0
        self.last_label_ts = 0.0

    # ---- events
    def on_score(self, s: float, ts: float, now: float) -> Transition | None:
        self.n1 += 1
        self.m1.update(s)
        self._track("M1", self.m1.factor, ts)
        self.calm = self.calm + 1 if self.m1.sr < self.cfg["m1"]["clear_below"] else 0
        t = self._escalate() or self._clear(now)
        if self.state == "HEALTHY":
            self.m1.grow(s)
        return t

    def on_commit_outcome(self, e: int, ts: float) -> Transition | None:
        self.n2 += 1
        self.m2.update(e)
        self._track("M2", self.m2.factor, ts)
        return self._escalate()

    def on_gold_label(self, now: float) -> None:
        self.commits_since_label, self.last_label_ts = 0, now

    def on_autonomous_commit(self) -> None:
        self.commits_since_label += 1

    def stale(self, now: float) -> bool:
        c = self.cfg["staleness"]
        return self.commits_since_label >= c["min_commits"] and now - self.last_label_ts >= c["no_label_s"]

    def check_stale(self, now: float) -> Transition | None:
        if self.state != "HEALTHY" or not self.stale(now):
            return None
        return self._move("WATCH", "STALENESS", now - self.last_label_ts, float(self.cfg["staleness"]["no_label_s"]))

    def on_certificate(self, cert: tuple[str, ...], now: float) -> Transition | None:
        """New certificate: detectors restart from zero, the reference stays (it is the certificate's Calib window)."""
        src = self.state
        self.m1.reset()
        self.m2.reset()
        self.state, self.pending_cert, self.cert, self.calm = "HEALTHY", False, cert, 0
        self.cusum = {"M1": (0.0, None), "M2": (0.0, None)}
        self.on_gold_label(now)
        return Transition(src, "HEALTHY", None, None, None, None) if src != "HEALTHY" else None

    # ---- transitions
    def _track(self, det: str, factor: float, ts: float) -> None:
        """Onset of a shift = where the best-suffix product of betting factors starts: a CUSUM W <- max(W, 1) x f, whose last visit to its
        floor (W <= 1) is the change-point estimate. Unlike SR, which idles around 1 to 3 under the null, W returns to its floor quickly."""
        w, start = self.cusum[det]
        self.cusum[det] = (max(w, 1.0) * factor, ts if w <= 1.0 else start)

    def _escalate(self) -> Transition | None:
        c1, c2 = self.cfg["m1"], self.cfg["m2"]
        for dst, t1, t2 in (("REVOKED", c1["revoke"], c2["revoke"]), ("ALERT", c1["alert"], c2["alert"]), ("WATCH", c1["watch"], None)):
            hits = [(sr / thr, det, sr, thr) for det, sr, thr in (("M1", self.m1.sr, t1), ("M2", self.m2.sr, t2)) if thr is not None and sr >= thr]
            if hits:
                _, det, sr, thr = max(hits)
                return self._move(dst, det, sr, thr) if RANK[dst] > RANK[self.state] else None
        return None

    def _clear(self, now: float) -> Transition | None:
        # M1 only (10.5 d); an unmonitored cell (stale, or never certified) must not look healthy again
        if self.state != "WATCH" or self.pending_cert or self.calm < self.cfg["m1"]["clear_decisions"] or self.stale(now):
            return None
        return self._move("HEALTHY", "M1", self.m1.sr, float(self.cfg["m1"]["clear_below"]))

    def _move(self, dst: CellState, det: str, value: float, thr: float) -> Transition:
        src, self.state = self.state, dst
        return Transition(src, dst, det, value, thr, None if dst == "HEALTHY" else self.cusum.get(det, (0.0, None))[1])

    # ---- checkpoint
    def dump(self) -> dict[str, Any]:
        version, internal, gauss = self.m1.rng.getstate()
        return {"state": self.state, "pending_cert": self.pending_cert, "cert": self.cert, "calm": self.calm, "n1": self.n1, "n2": self.n2,
                "sr1": self.m1.sr, "sr2": self.m2.sr, "ref": list(self.m1.ref), "rng": [version, list(internal), gauss], "cusum": self.cusum,
                "commits_since_label": self.commits_since_label, "last_label_ts": self.last_label_ts}

    def load(self, d: Mapping[str, Any]) -> None:
        self.state, self.pending_cert, self.calm, self.n1, self.n2 = d["state"], d["pending_cert"], d["calm"], d["n1"], d["n2"]
        self.cert = None if d["cert"] is None else tuple(d["cert"])
        self.m1.reset(d["ref"])
        self.m1.sr, self.m2.sr = d["sr1"], d["sr2"]
        version, internal, gauss = d["rng"]
        self.m1.rng.setstate((version, tuple(internal), gauss))
        self.cusum = {k: (v[0], v[1]) for k, v in d["cusum"].items()}
        self.commits_since_label, self.last_label_ts = d["commits_since_label"], d["last_label_ts"]


class Sentinel:
    def __init__(self, catalog: Catalog, det_cfg: Mapping[str, Any], clock: Clock, checkpoint_dir: Path, *,
                 emit: Callable[[SentinelEvent], Awaitable[None]], audit_append: AuditAppend, canary: Callable[[CanaryRequest], Awaitable[None]]) -> None:
        self.catalog, self.cfg, self.clock, self.dir = catalog, det_cfg, clock, checkpoint_dir
        self.emit, self.audit_append, self.canary = emit, audit_append, canary
        self.machines = {c: CellMachine(c, catalog.alpha_of(c, 0), det_cfg) for c in catalog.cells()}
        self.events = 0

    async def start(self, registry: RegistrySnapshot | None = None) -> None:
        """Restore the checkpoint (none, or from another catalog: every cell WATCH until recertified), then take `registry` as the baseline:
        its certificates are the ones the restored detectors already belong to, so they do not count as new."""
        saved = checkpoint.load(self.dir)
        now = self.clock.now()
        if saved is not None and saved.get("catalog_version") == self.catalog.version:
            for cell, m in self.machines.items():
                if cell in saved["cells"]:
                    m.load(saved["cells"][cell])
        for m in self.machines.values():
            if m.pending_cert:
                m.last_label_ts = now
        if registry is not None:
            await self._sync(registry, baseline=True)

    def set_reference(self, cell: str, scores: Sequence[float]) -> None:
        self.machines[cell].m1.reset(scores)

    async def on_audit(self, rec: AuditRecord) -> None:
        p = rec.payload
        m = self.machines.get(p["cell"]) if rec.type == "DECISION" and p["mode"] == "LIVE" else None
        if m is None:
            return
        if p["decision"]["kind"] == "COMMIT":
            m.on_autonomous_commit()
        if p["stage_index"] == 0 and p.get("sp") is not None:
            await self._after_event(p["cell"], m.on_score(p["sp"]["s"], rec.ts or self.clock.now(), self.clock.now()))

    async def on_outcome(self, o: LabeledOutcome) -> None:
        m = self.machines.get(o.cell) if o.cell and o.label_tier in ("G", "A") else None
        if m is None:
            return
        m.on_gold_label(self.clock.now())
        if o.decision_kind == "COMMIT" and o.autonomous:
            await self._after_event(str(o.cell), m.on_commit_outcome(o.y_final, o.ts or self.clock.now()))

    async def on_registry(self, snap: RegistrySnapshot) -> None:
        await self._sync(snap, baseline=False)

    async def run_heartbeat(self, clock_sleep: Callable[[float], Awaitable[None]] | None = None, interval_s: float = 2.0) -> None:
        await heartbeat.run(self.emit, self._tick, self.clock.now, clock_sleep or self.clock.sleep, interval_s)

    def states(self) -> dict[str, dict[str, Any]]:
        now, c1, c2 = self.clock.now(), self.cfg["m1"], self.cfg["m2"]
        return {c: {"state": m.state, "srm1": m.m1.sr, "srm2": m.m2.sr, "n_m1": m.n1, "n_m2": m.n2, "reference_n": len(m.m1.ref), "calm": m.calm,
                    "commits_since_label": m.commits_since_label, "label_age_s": now - m.last_label_ts, "stale": m.stale(now), "pending_cert": m.pending_cert,
                    "thresholds": {"m1": {k: c1[k] for k in ("watch", "alert", "revoke")}, "m2": {k: c2[k] for k in ("alert", "revoke")}}}
                for c, m in self.machines.items()}

    # ---- internals
    async def _sync(self, snap: RegistrySnapshot, *, baseline: bool) -> None:
        now = self.clock.now()
        for cell, m in self.machines.items():
            sig = tuple(r.cert_id for r in snap.cells[cell].ladder) if cell in snap.cells else ()
            if baseline and m.cert is None:
                m.cert = sig
            elif sig and sig != m.cert:
                t = m.on_certificate(sig, now)
                self._save()
                if t is not None:
                    await self._announce(cell, t)

    async def _tick(self) -> None:
        now = self.clock.now()
        for cell, m in self.machines.items():
            t = m.check_stale(now)
            if t is not None:
                await self._announce(cell, t)

    async def _after_event(self, cell: str, t: Transition | None) -> None:
        self.events += 1
        if t is not None:
            await self._announce(cell, t)
        elif self.events % self.cfg["checkpoint_every"] == 0:
            self._save()

    async def _announce(self, cell: str, t: Transition) -> None:
        ev = SentinelEvent(type="STATE_CHANGE", cell=cell, from_state=t.src, to_state=t.dst, detector=t.detector,  # type: ignore[arg-type]
                           value=t.value, threshold=t.threshold, shift_onset_ts=t.onset, ts=self.clock.now())
        # also saved on every transition: a checkpoint up to 100 events old must never bring an ALERT back as HEALTHY
        self._save()
        await self.audit_append("SENTINEL_EVENT", ev.model_dump(mode="json"), ev.trace_id)
        await self.emit(ev)
        if t.dst != "HEALTHY":
            c = self.cfg["canary"]
            await self.canary(CanaryRequest(cell=cell, rate_per_min=c["rate_per_min"], duration_s=c["duration_s"], ts=ev.ts))

    def _save(self) -> None:
        checkpoint.save(self.dir, {"catalog_version": self.catalog.version, "events": self.events,
                                   "cells": {c: m.dump() for c, m in self.machines.items()}})
