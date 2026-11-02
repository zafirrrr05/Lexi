"""Composition root. Every edge of section 8 that crosses a component boundary is made here, because packages may not import their downstream
neighbours (TP1): audit -> labels / sentinel / console, gate -> ledger / planner, planner -> gateway, sentinel -> authority -> gate, and so on.

    lexi serve        the server process: C1-C10, C12-C14 and the console, on one asyncio loop (6.2)
    lexi twin         the Twin (C11) as its own OS process; it reaches C1 over WebSocket like a telephone bridge
    lexi certify      initial certification, the calibration cycle of D11 (a-e)
    lexi replay       replay of audit segments through the pure decision core (C15, E1)
    lexi audit-verify check the hash chain of the audit log (D16)
    lexi bench        LEXI-Bench: baselines, ablations, shift and null experiments, the report (C15)
"""

import argparse
import asyncio
import json
import logging
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import APIRouter, FastAPI

from lexi.adapters.cache import ProviderCache
from lexi.adapters.evon import EvonClient
from lexi.adapters.prisma import PrismaClient
from lexi.adapters.timbre import TimbreClient
from lexi.audit.log import AuditLog
from lexi.audit.verify import verify_dir
from lexi.authority.registry import Authority
from lexi.authority.rescore import rescored
from lexi.bench.baselines import match_theta, min_conf_critical
from lexi.bench.live_run import latency
from lexi.bench.replay import replay_decisions
from lexi.bench.report import evaluate, write
from lexi.common.catalog import Catalog, load_catalog, load_yaml
from lexi.common.clock import SystemClock
from lexi.common.dates import today_ist
from lexi.common.ids import det_id
from lexi.common.logging import metrics_snapshot, setup_logging
from lexi.common.types import (
    AuditRecord,
    CalibrationRecord,
    CanaryRequest,
    CellStats,
    CommitRequest,
    Decision,
    EscalationTicket,
    GoldLabel,
    HealthSignal,
    HumanCommit,
    HumanVerdict,
    LabeledOutcome,
    LedgerView,
    ProbeOutcome,
    Receipt,
    RegistrySnapshot,
    ScenarioCommand,
    ScoredProposal,
    SentinelEvent,
    SpeechPlan,
    TurnInput,
    UndoRequest,
)
from lexi.evidence.builder import Evidence
from lexi.evidence.fit_confusion import fit as fit_confusion
from lexi.evidence.ipk import ipk
from lexi.gate.act import Gate
from lexi.gate.modes import HealthState
from lexi.gateway.server import Gateway
from lexi.gateway.session import Ports
from lexi.gateway.tts_player import TtsPlayer
from lexi.labelstore.ingest import LabelStore
from lexi.ledger.commit import Ledger
from lexi.ledger.store import SIGN
from lexi.parser.parser import Parser
from lexi.probe.fit_models import fit as fit_probe_models
from lexi.probe.plan import Planner
from lexi.profiler.profile import Profiler
from lexi.risk.bundle import ScorerBundle
from lexi.risk.probe_update import ProbeParams
from lexi.risk.scorer import RiskEngine
from lexi.risk.train import train
from lexi.sentinel.machine import Sentinel
from lexi.twin.scripts import make_accounts

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("lexi")
NO_KEEPALIVE = httpx.Limits(max_keepalive_connections=0)  # a loop busy with CPU work can let uvicorn close an idle connection just as the next request arrives
MIN_RATE_RECORDS, MIN_RATE = 30, 0.05  # K15's accepted-share estimate: records needed to trust a cell's own post-onset data, and its floor
SPLIT_SHARE = {"Train": 5 / 15, "Valid": 1.5 / 15, "Calib": 6 / 15, "Test": 2.5 / 15}  # 4.7
Publish = Callable[[str, Any], None]
STATS_EVERY_S, RECERT_EVERY_S = 1.0, 2.0  # wall time


def load_env(path: Path) -> None:
    """KEY=value lines of .env into the environment (the file is git-ignored; .env.example lists the names)."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#") and value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class System:
    """C1-C10, C12-C14 and the console on one loop, wired by the edges of section 8."""

    def __init__(self, cfg: dict[str, Any], root: Path = ROOT, *, mode: str | None = None, profile: str | None = None, speed: float | None = None) -> None:
        self.cfg, self.root = cfg, root
        self.mode = mode or cfg["mode"]
        self.paths = {k: root / v for k, v in cfg["paths"].items()}
        self.catalog: Catalog = load_catalog(profile=profile)
        self.det_cfg = load_yaml("detectors.yaml")
        self.clock = SystemClock(speed or cfg["clock_speed"])
        self.health = HealthState()
        self.feed: list[Publish] = []  # the console subscribes here (K03, K06, K23, K26, H14, H18, H23)
        self.tickets: dict[str, EscalationTicket] = {}
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()  # async consumers of the audit and outcome streams, in order
        self._tasks: list[asyncio.Task[None]] = []
        self._http = httpx.AsyncClient(timeout=10, limits=NO_KEEPALIVE)

    # ---- start / stop
    async def start(self) -> None:
        cfg, p = self.cfg, self.paths
        for d in (p["data"], p["artifacts"]):
            d.mkdir(parents=True, exist_ok=True)
        prov = cfg["providers"]
        self.cache = ProviderCache(p["providers_cache"], prov["cache"])
        self.prisma = PrismaClient(prov["mode"], prov["prisma"], self.cache)
        self.timbre = TimbreClient(prov["mode"], prov["timbre"], self.cache)
        self.evon = EvonClient(prov["mode"], prov["evon"], self.cache)
        self.audit = AuditLog(p["audit"], self.clock, **cfg["audit"], on_health=self._on_health)
        await self.audit.start()
        led = cfg["ledger"]
        self.ledger = Ledger(p["ledger"], self.catalog, self.clock, self.audit, undo_window_s=led["undo_window_s"], delivery_timeout_s=led["delivery_timeout_s"],
                             unhealthy_s=led["unhealthy_s"], on_receipt=self._on_receipt, on_view=self._on_view, on_health=self._on_health)
        self._seed_accounts()
        await self.ledger.start()
        self.labels = LabelStore(p["labels"], self.catalog, self.clock)
        self.labels.on_outcome(self._on_outcome)
        self.labels.on_call(lambda c: self._emit("call", c))
        self.audit.subscribe(self._on_audit)
        self.authority = Authority(self.catalog, self.det_cfg, self.clock, p["certs"], audit_append=self.audit.append, publish=self._publish,
                                   lease_ttl_s=cfg["lease"]["ttl_s"])
        self.sentinel = Sentinel(self.catalog, self.det_cfg, self.clock, p["checkpoints"], emit=self._sentinel_event, audit_append=self.audit.append,
                                 canary=self._canary)
        self.reload_artifacts()
        await self.authority.start()
        await self.sentinel.start(self.authority.read())
        self.gate = Gate(self.catalog, self.mode, self.clock, registry=self.authority.read, scorer_version=lambda: self.engine.scorer_version,  # type: ignore[arg-type]
                         audit_append=self.audit.append, commit=self._commit, to_planner=self._to_planner, ticket=self._ticket, health=self.health,
                         registry_max_age_s=cfg["lease"]["registry_max_age_s"])
        gw = cfg["gateway"]
        self.gateway = Gateway(Ports(cfg=gw, clock=self.clock, profiler=lambda: Profiler(self.catalog), prisma=self.prisma, tts=TtsPlayer(self.timbre),
                                     open_tail=lambda words, lang: self.evidence.open_tail(list(words), lang), on_turn=self.on_turn,
                                     signal=self.ledger.signal, on_control=lambda c: self._emit("control", c)))
        if self.engine.bundle is not None and self.labels.counts().get("Valid"):  # a certified system restarting: grids and the baseline threshold as they were
            self.plan_grids()
            self.tune_baseline()
        self._tasks = [asyncio.create_task(self._consume()), asyncio.create_task(self.sentinel.run_heartbeat(self.clock.sleep, cfg["lease"]["heartbeat_s"])),
                       asyncio.create_task(self.authority.run_lease_monitor()), asyncio.create_task(self._sweep()), asyncio.create_task(self._publish_stats()),
                       asyncio.create_task(self._recertify())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.ledger.close()
        await self.audit.close()
        self.labels.close()
        self.authority.store.close()
        self.cache.close()
        await self._http.aclose()

    def _seed_accounts(self) -> None:
        """The SME accounts of the demo. The Twin derives the same accounts from the same seed, so callers and ledger agree on the registry."""
        tw = self.cfg["twin"]
        specs = [*make_accounts(tw["seed"], self.catalog.languages, tw["accounts_per_language"]),
                 *make_accounts(tw["seed"], self.catalog.languages, tw["accounts_per_language"], unseen=True)]
        rows = []
        for a in specs:
            row = a.ledger_row()
            row["parties"] = [{**x, "ipk": ipk(x["display"])} for x in row["parties"]]
            rows.append(row)
        self.ledger.seed(rows)
        self.accounts = [{"id": r["account"], "lang": a.lang, "parties": [x["display"] for x in r["parties"]]} for r, a in zip(rows, specs, strict=True)]

    # ---- artifacts (C3 tables, C8 probe models, C5 scorer)
    def reload_artifacts(self) -> None:
        art = self.paths["artifacts"]
        confusion, probe, active = art / "confusion.json", art / "probe_models.json", art / "scorer" / "ACTIVE"
        self.evidence = Evidence.load(confusion_path=confusion if confusion.exists() else None)
        params = ProbeParams.load(probe if probe.exists() else None)
        bundle = ScorerBundle.load(art / "scorer" / active.read_text().strip()) if active.exists() else None
        self.engine = RiskEngine(self.catalog, bundle, params)
        self.planner = Planner(self.catalog, params, self.clock)
        self.parser = Parser(self.catalog, self.evon.complete, timeout_ms=self.cfg["timeouts_ms"]["evon"])

    def fit(self) -> ScorerBundle:
        """D11 b: confusion tables (C3), probe response models (C8) and the scorer (C5) from Train, isotonic and tau from Valid. Artifacts are versioned."""
        art = self.paths["artifacts"]
        (art / "confusion.json").write_text(json.dumps(fit_confusion(self.labels.training_data("confusion", "Train").records)))
        (art / "probe_models.json").write_text(json.dumps(fit_probe_models(self.labels.training_data("probe_models", "Train").records)))
        bundle = train(self.labels.training_data("scorer", "Train").records, self.labels.training_data("scorer", "Valid").records, art / "scorer")
        (art / "scorer" / "ACTIVE").write_text(bundle.version)
        self.reload_artifacts()
        return bundle

    def plan_grids(self) -> None:
        """Fix every certification grid from Valid before a Calib call is made (10.3); COLLECT then probes down to each cell's lambda_min (C7)."""
        valid = self._by_cell("Valid")
        if self.engine.bundle:
            valid = {c: rescored(rs, self.engine.bundle.score_features, self.engine.scorer_version) for c, rs in valid.items()}
        cells = self.catalog.cells()
        planned = max(1, round(self.catalog.calls * SPLIT_SHARE["Calib"] / len(cells)))  # ponytail: an even split of the Calib budget over the cells
        self.authority.plan(valid, dict.fromkeys(cells, planned))
        self.gate.lambda_min = {c: lam for c in cells if (lam := self.authority.lambda_min(c)) is not None}

    async def issue(self, only: list[str] | None = None) -> Any:
        """D11 d: CERT-v1 per cell and rung from Calib, then one atomic (scorer, registry) swap. The sentinel starts from the Calib references (K14).
        `only` is a recertification (D12 step 4): the cells that were taken to ALERT, from their post-onset records."""
        bundle = self.engine.bundle
        assert bundle is not None, "certify needs a fitted scorer"
        self.plan_grids()
        report = await self.authority.issue(bundle.version, self._by_cell("Calib"), self._by_cell("Valid"), rescore=bundle.score_features, only=only)
        for cell, scores in report.reference.items():
            self.sentinel.set_reference(cell, scores)
        self.tune_baseline()
        return report

    def tune_baseline(self) -> None:
        """B1 (16.3): theta_b matched on Valid to the share of calls that LEXI's certified RG0 threshold accepts."""
        reg, bundle = self.authority.read(), self.engine.bundle
        recs = [r for rs in self._by_cell("Valid").values() for r in rs]
        if reg is None or bundle is None or not recs:
            return
        recs = rescored(recs, bundle.score_features, self.engine.scorer_version)
        lam = {c: next((r.lam for r in e.ladder if r.rung == 0), None) for c, e in reg.cells.items()}
        accepted = sum(1 for r in recs if (lam.get(r.cell) is not None and r.scores[0] <= lam[r.cell]))  # type: ignore[operator]
        self.gate.theta_b = match_theta([min_conf_critical(r.feature_vectors[0]) for r in recs], accepted / len(recs))

    def cell_stats(self) -> CellStats:
        """K15/K16. Deficits count post-onset records only; the accepted share per rung comes from the cell's own post-onset records once there are enough,
        from Valid before that."""
        since, reg = self.authority.onsets(), self.authority.read()
        rates: dict[str, list[float]] = {}
        for cell in self.catalog.cells():
            recs = self.labels.first_scores("Calib", cell, since.get(cell))
            if len(recs) < MIN_RATE_RECORDS:
                recs = self.labels.first_scores("Valid", cell)
            ladder = {r.rung: r.lam for r in reg.cells[cell].ladder} if reg and cell in reg.cells else {}
            lams = [ladder.get(k, self.authority.grid_start(cell, k)) for k in range(len(self.catalog.rungs))]
            rates[cell] = [max(MIN_RATE, sum(s0 <= lam for s0 in recs) / len(recs)) if recs and lam is not None else 0.5 for lam in lams]
        return self.labels.cell_stats(rates, since)

    def _by_cell(self, split: str) -> dict[str, list[CalibrationRecord]]:
        grouped: dict[str, list[CalibrationRecord]] = defaultdict(list)
        for r in self.labels.calibration_records(split):
            grouped[r.cell].append(r)
        return grouped

    # ---- the hot path: H05 -> C3 -> C4 / C8 -> C5 -> C7
    async def on_turn(self, turn: TurnInput) -> None:
        t0 = time.perf_counter()
        eb = self.evidence.build(turn)
        lat = {"c3": (time.perf_counter() - t0) * 1000}
        if eb.mode != "NEW_COMMAND":
            answer = self.planner.on_answer(eb)  # H07
            if isinstance(answer, UndoRequest):
                await self.ledger.undo(answer)  # H20
                return
            if isinstance(answer, ProbeOutcome):
                await self.gate.on_scored(self.engine.update(answer), lat, answer, turn)  # H09, H10
                return
            turn = self.gateway.renew(turn)  # CONTINUE: the same words are a new command (C8 step 3)
            eb = self.evidence.build(turn)
        t1 = time.perf_counter()
        ap = await self.parser.parse(eb)  # H06
        t2 = time.perf_counter()
        sp = self.engine.score(eb, ap)  # H08
        lat |= {"c4": (t2 - t1) * 1000, "c5": (time.perf_counter() - t2) * 1000}
        await self.gate.on_scored(sp, lat, None, turn)  # H10

    async def _commit(self, req: CommitRequest) -> None:
        await self.ledger.commit(req)  # H12; the receipt comes back through _on_receipt (H16)

    async def _to_planner(self, session_id: str, decision: Decision, sp: ScoredProposal) -> None:
        plan: SpeechPlan = self.planner.plan(session_id, decision, sp)  # H13
        await self.gateway.speak(session_id, plan)  # H21

    async def _on_receipt(self, receipt: Receipt) -> None:
        await self.gateway.speak(receipt.session_id, self.planner.plan_receipt(receipt))  # H16, H21
        if receipt.status == "REJECTED":  # D10: no spoken success, and a human looks at it
            await self._ticket(EscalationTicket(
                trace_id=receipt.session_id, ts=self.clock.now(), ticket_id=det_id("TKT", receipt.entry_id), decision_id=receipt.entry_id,
                session_id=receipt.session_id, reason_code="LEDGER_ERROR", cell=None, candidates={},
                evidence_summary={"intent": receipt.intent, "party": receipt.party_display, "amount": receipt.amount_inr, "date": receipt.date}))

    async def _on_view(self, session_id: str, view: LedgerView) -> None:
        self.gateway.on_view(session_id, view)  # H17
        self._emit("view", {"session_id": session_id, "view": view})  # H18

    async def _ticket(self, ticket: EscalationTicket) -> None:
        self.tickets[ticket.ticket_id] = ticket
        self._emit("ticket", ticket)  # H14

    # ---- assurance streams
    def _on_audit(self, rec: AuditRecord) -> None:
        self.labels.ingest_audit(rec)  # K01
        self._queue.put_nowait(("audit", rec))  # K02
        self._emit("audit", rec)  # K03

    def _on_health(self, sig: HealthSignal) -> None:
        setattr(self.health, "audit_healthy" if sig.component == "C12" else "ledger_healthy", sig.healthy)  # K30, K31
        self._emit("health", sig)

    def _on_outcome(self, o: LabeledOutcome) -> None:
        self._queue.put_nowait(("outcome", o))  # K13
        self._emit("outcome", o)

    def _publish(self, snap: RegistrySnapshot) -> None:
        self._queue.put_nowait(("registry", snap))  # K21
        self._emit("registry", snap)  # K23

    async def _sentinel_event(self, ev: SentinelEvent) -> None:
        await self.authority.on_sentinel(ev)  # K25
        reg = self.authority.read()
        self._emit("sentinel", {"event": ev, "states": self.sentinel.states(), "lease_expiry": reg.lease_expiry if reg else None})  # K26

    async def _canary(self, req: CanaryRequest) -> None:
        await self._twin("/canary", req.model_dump(mode="json"))  # K28

    async def _consume(self) -> None:
        while True:
            kind, item = await self._queue.get()
            try:
                if kind == "audit":
                    await self.sentinel.on_audit(item)
                elif kind == "outcome":
                    await self.sentinel.on_outcome(item)
                else:
                    await self.sentinel.on_registry(item)
            except Exception:
                log.exception("sentinel failed on %s", kind)

    async def _publish_stats(self) -> None:
        while True:
            await asyncio.sleep(STATS_EVERY_S)
            try:
                self._emit("cellstats", self.cell_stats())  # K16
            except Exception:
                log.exception("cell statistics failed")

    async def _recertify(self) -> None:
        """D12 step 4: a cell the sentinel took to ALERT or REVOKED gets a new certificate as soon as its post-onset Calib records meet the target (K15
        deficit zero). Only live servers recertify: a COLLECT server is itself the bootstrap."""
        while True:
            await asyncio.sleep(RECERT_EVERY_S)
            cells = self.authority.reissue_cells()
            if self.mode != "LIVE" or not cells:
                continue
            try:  # one failed attempt must not end the loop: the next tick tries again with more records
                stats = self.cell_stats()
                ready = [c for c in cells if stats.cells[c].deficit_by_rung[0] == 0]
                if ready:
                    report = await self.issue(only=ready)
                    log.info("recertified %s: %d certificates, registry v%s", ready, len(report.certificates), report.version)
            except Exception:
                log.exception("recertification failed")

    async def _sweep(self) -> None:
        while True:
            await self.clock.sleep(5)
            self.planner.expire(self.clock.now())

    def _emit(self, kind: str, payload: Any) -> None:
        for sub in self.feed:
            sub(kind, payload)

    # ---- edges from the console and the Twin
    async def human_commit(self, hc: HumanCommit) -> Receipt:
        receipt = await self.ledger.human_commit(hc)  # K07
        if self.tickets.pop(hc.ticket_id, None) is not None:
            self._emit("ticket_closed", {"ticket_id": hc.ticket_id, "status": receipt.status})
        return receipt

    def verdict(self, v: HumanVerdict) -> None:
        self.labels.ingest_verdict(v)  # K06

    async def command(self, cmd: ScenarioCommand) -> dict[str, Any]:
        return await self._twin("/command", cmd.model_dump(mode="json"))  # K08

    async def _twin(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        tw = self.cfg["twin"]
        try:
            r = await self._http.post(f"http://{tw['host']}:{tw['control_port']}{path}", json=body)
            return dict(r.json())
        except (httpx.HTTPError, ValueError) as e:
            return {"error": f"twin not reachable: {e}"}

    async def twin_status(self) -> dict[str, Any]:
        tw = self.cfg["twin"]
        try:
            return dict((await self._http.get(f"http://{tw['host']}:{tw['control_port']}/status")).json())
        except (httpx.HTTPError, ValueError):
            return {"running": False, "reachable": False}

    def bridge(self) -> APIRouter:
        """What the Twin process calls across the process boundary: K05 GoldLabel and K15 CellStats. Handlers are `async` on purpose: the label store
        is one SQLite connection and must only be touched from the event loop thread."""
        r = APIRouter(prefix="/bridge")

        @r.post("/gold")
        async def gold(g: GoldLabel) -> dict[str, bool]:
            self.labels.ingest_gold(g)
            self._emit("gold", g)
            return {"ok": True}

        @r.get("/cellstats")
        async def cellstats() -> dict[str, Any]:
            return self.cell_stats().model_dump(mode="json")

        @r.get("/counts")
        async def counts() -> dict[str, int]:
            return self.labels.counts()

        return r

    def app(self) -> FastAPI:
        from lexi.console.app import (  # noqa: PLC0415  (the console is optional for headless runs)
            Hooks,
            create_app,
        )

        app, publish = create_app(Hooks(human_commit=self.human_commit, verdict=self.verdict, command=self.command, twin=self.twin_status, state=self.state,
                                        results_dir=self.paths["results"],
                                        tally={"alpha": {c: self.catalog.alpha_of(c, 0) for c in self.catalog.cells()}, "cap_inr": self.catalog.limits.max_autonomous_amount_inr,
                                               "sign": SIGN}))
        self.feed.append(publish)
        app.include_router(self.gateway.router())
        app.include_router(self.bridge())
        app.add_api_route("/metrics", metrics_snapshot, methods=["GET"])  # in-process counters and latency percentiles (13)
        return app

    def state(self) -> dict[str, Any]:
        """What a console that has just connected needs to draw itself."""
        return {"mode": self.mode, "catalog": {"version": self.catalog.version, "profile": self.catalog.profile, "cells": self.catalog.cells()},
                "registry": self.authority.read(), "sentinel": self.sentinel.states(), "tickets": list(self.tickets.values()),
                "audit_head": self.audit.head, "scorer_version": self.engine.scorer_version, "health": self.health,
                "clock": {"now": self.clock.now(), "speed": self.clock.speed}, "accounts": self.accounts,
                "providers": self.cfg["providers"]["mode"], "undo_window_s": self.cfg["ledger"]["undo_window_s"], "lease_ttl_s": self.cfg["lease"]["ttl_s"]}


def load_runtime(root: Path = ROOT) -> dict[str, Any]:
    load_env(root / ".env")
    return load_yaml("runtime.yaml", root / "config")


# ---------------------------------------------------------------------------------------------------------------- commands
async def cmd_serve(args: argparse.Namespace) -> None:
    cfg = load_runtime()
    system = System(cfg, mode=args.mode, profile=args.profile, speed=args.speed)
    await system.start()
    srv = cfg["server"]
    server = uvicorn.Server(uvicorn.Config(system.app(), host=srv["host"], port=srv["port"], log_level="warning", ws_per_message_deflate=False, ws_ping_interval=None))
    print(f"lexi serve: http://{srv['host']}:{srv['port']}  mode={system.mode}  profile={system.catalog.profile}  scorer={system.engine.scorer_version}", flush=True)
    try:
        await server.serve()
    finally:
        await system.stop()


async def cmd_twin(args: argparse.Namespace) -> None:
    from lexi.twin.caller_model import CallerParams  # noqa: PLC0415
    from lexi.twin.canary import Canaries  # noqa: PLC0415
    from lexi.twin.credits import Governor  # noqa: PLC0415
    from lexi.twin.runner import Driver, TwinRunner, create_app  # noqa: PLC0415
    from lexi.twin.scripts import CARRIERS, template_pool  # noqa: PLC0415

    cfg = load_runtime()
    catalog = load_catalog(profile=args.profile)
    prov, tw, d = cfg["providers"], cfg["twin"], cfg["twin_defaults"]
    speed = args.speed or d["speed"]
    clock = SystemClock(args.clock_speed or args.speed or cfg["clock_speed"])  # one --speed moves call time and the clock together, as on the server
    paths = {k: ROOT / v for k, v in cfg["paths"].items()}
    cache = ProviderCache(paths["providers_cache"], prov["cache"])
    base = f"http://{cfg['server']['host']}:{cfg['server']['port']}"
    http = httpx.AsyncClient(timeout=30, limits=NO_KEEPALIVE)

    async def send_gold(g: GoldLabel) -> None:
        (await http.post(f"{base}/bridge/gold", json=g.model_dump(mode="json"))).raise_for_status()

    async def stats() -> CellStats | None:
        try:
            return CellStats.model_validate((await http.get(f"{base}/bridge/cellstats")).json())
        except (httpx.HTTPError, ValueError):
            return None

    synth = TimbreClient(prov["mode"], prov["timbre"], cache)
    runner = TwinRunner(f"ws://{cfg['server']['host']}:{cfg['server']['port']}/call", synth, cache, send_gold,
                        CallerParams(kappa=d["kappa"], silence=d["silence_prob"]), speed=speed, today=lambda: today_ist(clock.now()))
    accounts = {"train": make_accounts(tw["seed"], catalog.languages, tw["accounts_per_language"]),
                "unseen": make_accounts(tw["seed"], catalog.languages, tw["accounts_per_language"], unseen=True)}
    templates = await template_pool(EvonClient(prov["mode"], prov["evon"], cache).complete, CARRIERS)
    budget = prov["credits_total"] if prov["mode"] == "live" else math.inf  # the simulator costs nothing
    driver = Driver(runner, accounts, Governor(paths["data"] / "credits.json", budget, prov["credit_reserve"]),
                    Canaries(catalog.cells(), d["canary_rate_per_min"]), stats=stats, concurrency=args.concurrency or d["concurrency"], seed=tw["seed"],
                    templates=templates)
    server = uvicorn.Server(uvicorn.Config(create_app(driver), host=cfg["twin"]["host"], port=cfg["twin"]["control_port"], log_level="warning"))
    print(f"lexi twin: control http://{tw['host']}:{tw['control_port']}  speed={speed}", flush=True)
    sched = asyncio.create_task(driver.run())
    try:
        await server.serve()
    finally:
        sched.cancel()
        await http.aclose()


async def _wait(url: str, what: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=2) as http:
        while time.monotonic() < deadline:
            try:
                await http.get(url)
                return
            except httpx.HTTPError:
                await asyncio.sleep(0.3)
    raise RuntimeError(f"{what} did not come up at {url}")


async def drive(cfg: dict[str, Any], targets: dict[str, int], *, calib: bool, label: str) -> None:
    """Ask the Twin process for calls until the targets are met (K33) and wait for it, printing progress."""
    tw = cfg["twin"]
    base = f"http://{tw['host']}:{tw['control_port']}"
    if not any(targets.values()):
        return
    async with httpx.AsyncClient(timeout=30, limits=NO_KEEPALIVE) as http:
        await http.post(f"{base}/fill", json={"targets": targets, "calib": calib})
        t0 = time.monotonic()
        while True:
            await asyncio.sleep(2)
            st = (await http.get(f"{base}/status")).json()
            print(f"  {label}: {st.get('completed', 0)} calls done, {st.get('failed', 0)} failed, {time.monotonic() - t0:.0f}s", flush=True)
            if not st["filling"]:
                return


async def cmd_certify(args: argparse.Namespace) -> None:
    """D11: collect Train and Valid, fit, collect Calib by cell deficit, certify, collect Test (sealed). Starts the server and the Twin itself."""
    cfg = load_runtime()
    scale = args.scale
    system = System(cfg, mode="COLLECT", profile=args.profile, speed=args.speed)
    twin_speed = args.speed or cfg["clock_speed"]
    await system.start()
    srv, tw = cfg["server"], cfg["twin"]
    server = uvicorn.Server(uvicorn.Config(system.app(), host=srv["host"], port=srv["port"], log_level="warning", ws_per_message_deflate=False, ws_ping_interval=None))
    serving = asyncio.create_task(server.serve())
    cmd = [sys.executable, "-m", "lexi.cli", "twin", "--speed", str(twin_speed), "--clock-speed", str(twin_speed), "--concurrency", str(args.concurrency)]
    if args.profile:
        cmd += ["--profile", args.profile]
    twin = subprocess.Popen(cmd)
    try:
        await _wait(f"http://{srv['host']}:{srv['port']}/bridge/counts", "server")
        await _wait(f"http://{tw['host']}:{tw['control_port']}/status", "twin")
        total = system.catalog.calls * scale
        want = {s: round(total * share) for s, share in SPLIT_SHARE.items()}
        have = system.labels.counts()
        left = {s: max(0, want[s] - have.get(s, 0)) for s in want}
        print(f"profile {system.catalog.profile}: {want}", flush=True)
        if system.engine.bundle is None:
            await drive(cfg, {"Train": left["Train"], "Valid": left["Valid"]}, calib=False, label="Train/Valid")
            bundle = system.fit()
            print(f"fitted: scorer {bundle.version}, valid ECE {bundle.schema['valid_ece']:.4f}", flush=True)
        system.plan_grids()
        await drive(cfg, {"Calib": left["Calib"]}, calib=True, label="Calib")
        await drive(cfg, {"Test": left["Test"]}, calib=False, label="Test")
        report = await system.issue()
        print(json.dumps({c: r.model_dump(mode="json") for c, r in report.cells.items()}, indent=1), flush=True)
        print(f"certificates issued: {len(report.certificates)}  registry v{report.version}", flush=True)
        await asyncio.sleep(1)
    except BaseException:
        log.exception("certify aborted (twin exit code %s)", twin.poll())  # the cleanup below may take a while: say why first
        raise
    finally:
        twin.terminate()
        twin.wait(10)
        server.should_exit = True
        server.force_exit = True  # open WebSockets and SSE streams must not hold the shutdown
        await serving
        await system.stop()


def _failed_tests(*files: str) -> int:
    """How the report measures the SLO rows that the test suite owns: the number of failing tests in these files (a crash counts as one)."""
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], cwd=ROOT, capture_output=True, text=True, check=False)
    return int(m.group(1)) if (m := re.search(r"(\d+) failed", r.stdout)) else int(r.returncode != 0)


async def cmd_replay(args: argparse.Namespace) -> int:
    """RC7: the stored DECISION records, run through the pure decision core again, must come out bit-identical."""
    system = System(load_runtime(), mode="LIVE")
    await system.start()
    try:
        payloads = [r.payload for r in system.audit.range(1) if r.type == "DECISION"]
        checked, bad = replay_decisions(payloads[:: max(1, len(payloads) // args.sample)][: args.sample], system.catalog)
        print(json.dumps({"decisions_in_audit": len(payloads), "replayed": checked, "mismatches": bad}))
        return int(bad > 0)
    finally:
        await system.stop()


async def cmd_bench(args: argparse.Namespace) -> int:
    """C15: E1 replay of the sealed Test split, baselines, ablations and the SLO table, written to results/<tag>/. A tag can be used once (P5)."""
    system = System(load_runtime(), mode="LIVE")
    await system.start()
    try:
        registry = system.authority.read()
        if registry is None:
            raise SystemExit("no certificates yet: run `lexi certify` first")
        payloads = [r.payload for r in system.audit.range(1) if r.type == "DECISION"]
        checked, bad = replay_decisions(payloads[:: max(1, len(payloads) // 1000)][:1000], system.catalog)
        audit_tests, ledger_tests = _failed_tests("tests/fault/test_audit_faults.py", "tests/unit/test_audit_log.py"), _failed_tests(
            "tests/fault/test_ledger_faults.py", "tests/property/test_ledger_invariants.py", "tests/unit/test_ledger_flow.py")
        lat = latency([p for p in payloads if p["mode"] == "LIVE"])  # COLLECT decisions carry no audit ack, so only the live ones say anything about L1-L3
        measured = {"replay_mismatches": bad if checked else None, "audit_chain_ok": verify_dir(system.paths["audit"]).ok and audit_tests == 0, "ledger_invariant_breaks": ledger_tests,
                    "detection_delay_ok": _failed_tests("tests/stats/test_sentinel_stats.py::test_m2_detection_delay_median_rc5") == 0,
                    "commits_after_lease_loss": _failed_tests("tests/unit/test_authority.py::test_lease_expiry_means_no_autonomy_in_the_gate"),
                    "hallucinated_values": _failed_tests("tests/stats/test_no_hallucination.py"),
                    "decision_core_p99_ms": lat.get("core_p99_ms"), "c3_p99_ms": lat.get("c3_p99_ms"), "audit_ack_p99_ms": lat.get("audit_ack_p99_ms")}
        checks = {"decisions replayed through decide()": f"{checked} of {len(payloads)} in the audit", "audit records": system.audit.head.seq,
                  "failing audit tests": audit_tests, "failing ledger tests": ledger_tests, **{f"live {k}": round(v, 3) for k, v in lat.items()}}
        with tempfile.TemporaryDirectory() as work:
            report = evaluate(args.tag, system.labels.test_split("C15", args.tag).records, system.labels.joined("Valid"), system.labels.training_data("scorer", "Train").records,
                              system.labels.training_data("scorer", "Valid").records, registry.model_dump(mode="json"), system.catalog, load_yaml("slo.yaml"), Path(work),
                              {"measured": measured, "groups": {"checks": checks}, "valid_ece": system.engine.bundle.schema["valid_ece"] if system.engine.bundle else None})
        print(f"{write(report, system.paths['results'])}  LEXI-verified: {report.passed}")
        return 0
    finally:
        await system.stop()


def cmd_audit_verify(_: argparse.Namespace) -> int:
    result = verify_dir(ROOT / load_runtime()["paths"]["audit"])
    print(json.dumps(result._asdict()))
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lexi", description="LEXI: certified autonomy for voice agents")
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the server and the console")
    serve.add_argument("--mode", choices=["LIVE", "COLLECT", "COLD_START"])
    certify = sub.add_parser("certify", help="initial certification (calibration cycle)")
    certify.add_argument("--scale", type=float, default=1.0, help="fraction of the profile's call budget to collect")
    certify.add_argument("--concurrency", type=int, default=8)
    twin = sub.add_parser("twin", help="run the synthetic caller twin")
    twin.add_argument("--concurrency", type=int)
    twin.add_argument("--clock-speed", type=float)
    for p in (serve, certify, twin):
        p.add_argument("--profile", choices=["FULL", "STANDARD", "LEAN"])
        p.add_argument("--speed", type=float, help="call time runs this many times faster than wall time (compresses the undo window too)")
    sub.add_parser("audit-verify", help="verify the audit hash chain")
    replay = sub.add_parser("replay", help="replay stored decisions through the pure core (RC7)")
    replay.add_argument("--sample", type=int, default=1000)
    bench = sub.add_parser("bench", help="LEXI-Bench: evaluate the sealed Test split and write results/<tag>/")
    bench.add_argument("--tag", required=True, help="release tag; the Test split is served once per tag")
    args = ap.parse_args(argv)
    setup_logging()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "audit-verify":
        return cmd_audit_verify(args)
    if args.cmd in ("replay", "bench"):
        return asyncio.run({"replay": cmd_replay, "bench": cmd_bench}[args.cmd](args))
    asyncio.run({"serve": cmd_serve, "certify": cmd_certify, "twin": cmd_twin}[args.cmd](args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
