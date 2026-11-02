"""The Twin as a caller (C11 step 6): dial C1 over WebSocket as a telephone bridge would, stream channel-processed audio, read C1's control messages
(SpeechPlan.expects) and play the next caller turn per CM-v1, then send the GoldLabel to C14.

Wire protocol on /call (docs/DECISIONS.md D-002): binary messages are 320-byte s16le frames (8 kHz, 20 ms); text messages are JSON. C1 -> Twin: CallControl.
Twin -> C1 (sim providers only): {"type": "announce", "utt_id", "clip_start_ms"}, which tells the simulated Prisma where a clip starts; the clip itself
is described in the shared provider cache. The line never goes quiet: when the caller has nothing to say the pump streams silence, as a phone line does.
"""

import asyncio
import json
import logging
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

import numpy as np
import websockets
from fastapi import FastAPI

from lexi.adapters.cache import ProviderCache
from lexi.adapters.timbre import TimbreClient
from lexi.common.hashing import digest
from lexi.common.types import CallControl, CanaryRequest, CellStats, GoldLabel, ScenarioCommand, TtsRequest
from lexi.twin.caller_model import Caller, CallerParams
from lexi.twin.canary import Canaries
from lexi.twin.channel import LineNoise, apply_channel
from lexi.twin.credits import Governor
from lexi.twin.scenarios import SCENARIOS, CallPlan, plan_call
from lexi.twin.scripts import CARRIERS, AccountSpec
from lexi.twin.verbalize import SpokenToken, tts_text

log = logging.getLogger("lexi.twin")
FRAME_MS, FRAME_BYTES = 20, 320
LEAD_S, TAIL_S = 0.15, 0.3  # silence around a clip, so the channel noise has a floor to be measured against
GoldSink = Callable[[GoldLabel], Awaitable[None]]
StatsSource = Callable[[], Awaitable[CellStats | None]]


class _Line:
    """The outgoing phone line: queued audio, silence when the queue is empty, one frame per 20 ms of call time (`speed` x faster than wall time)."""

    def __init__(self, ws: Any, speed: float, noise: LineNoise) -> None:
        self.ws, self.speed, self.noise, self.buf, self.sent_ms = ws, speed, noise, bytearray(), 0

    def end_ms(self) -> int:
        return self.sent_ms + len(self.buf) // 16

    def queue(self, pcm: bytes) -> None:
        self.buf += pcm

    def idle(self, ms: int) -> None:
        self.buf += self.noise.take(ms * 8)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        due = loop.time()
        while True:
            frame = bytes(self.buf[:FRAME_BYTES])
            frame += self.noise.take((FRAME_BYTES - len(frame)) // 2)
            del self.buf[:FRAME_BYTES]
            await self.ws.send(frame)
            self.sent_ms += FRAME_MS
            due += FRAME_MS / 1000 / self.speed
            await asyncio.sleep(max(0.0, due - loop.time()))


class TwinRunner:
    def __init__(self, url: str, synth: TimbreClient, cache: ProviderCache | None, send_gold: GoldSink, params: CallerParams, *,
                 speed: float = 1.0, today: Callable[[], date] = date.today) -> None:
        self.url, self.synth, self.cache, self.send_gold, self.params, self.speed, self.today = url, synth, cache, send_gold, params, speed, today
        self._babble: np.ndarray | None = None

    async def run_call(self, plan: CallPlan, tags: Sequence[str] = (), *, collect: bool = False) -> float | None:
        """One call. Returns the credits it used, or None if the call did not complete (no gold label is sent for it). A `collect` call is data
        generation: the system probes it down to lambda_min and never commits (D12 step 4)."""
        s = plan.script
        caller = Caller(s, plan.account, self.today(), random.Random(plan.seed), self.params)
        rng = np.random.default_rng(plan.seed)
        reaction = random.Random(plan.seed + 1)
        uri = f"{self.url}?session_id={plan.call_id}&account={plan.account.account}&lang={s.lang}" + ("&collect=true" if collect else "")
        try:
            async with asyncio.timeout(120 / self.speed + 60), websockets.connect(uri, max_size=None, open_timeout=10, compression=None, ping_interval=None) as ws:
                line = _Line(ws, self.speed, LineNoise(plan.channel, rng, await self._babble_source()))
                pump = asyncio.create_task(line.run())
                try:
                    n = 0
                    async for raw in ws:
                        ctl = CallControl.model_validate_json(raw)
                        reply = caller.respond(ctl.expects)
                        if reply.tokens:
                            gap = ctl.duration_ms * 0.6 if reply.barge_in else ctl.duration_ms + reaction.uniform(300, 900)
                            await self._speak(ws, line, plan, rng, reply.tokens, int(gap), n)
                            n += 1
                finally:
                    pump.cancel()
        except (websockets.WebSocketException, OSError, TimeoutError, ValueError) as e:
            log.warning("call %s failed: %s", plan.call_id, e)
            return None
        await self.send_gold(self._gold(plan, tags))
        return line.sent_ms / 60_000  # audio minutes: what the hosted recogniser bills

    async def _speak(self, ws: Any, line: _Line, plan: CallPlan, rng: np.random.Generator, tokens: list[SpokenToken], gap_ms: int, n: int) -> None:
        lang = plan.script.lang
        v = plan.voice
        audio = await self.synth.synth(TtsRequest(text=tts_text(tokens, lang), lang=lang, voice=v.name, rate=v.rate, pitch_semitones=v.pitch_st))
        clean = np.concatenate([np.zeros(int(LEAD_S * 8000)), np.frombuffer(audio.pcm_s16le, "<i2"), np.zeros(int(TAIL_S * 8000))])
        clip = apply_channel(clean, plan.channel, rng, await self._babble_source())
        line.idle(gap_ms)
        start = line.end_ms()
        if self.cache is not None and audio.word_timings:  # sim recogniser: describe the clip
            utt_id = int(digest([plan.call_id, n])[:12], 16)
            entry = {"seed": plan.seed + n, "lang": lang, "channel": {"snr_db": plan.channel.snr_db, "plc_fraction": plan.channel.loss_pct / 100},
                     "tokens": [{"kind": t.kind, "forms": {k: list(f) for k, f in t.forms.items()}, "t0": LEAD_S + t0, "t1": LEAD_S + t1}
                                for t, (_, t0, t1) in zip(tokens, audio.word_timings, strict=True)]}
            self.cache.put_sim_utterance(utt_id, entry)
            await ws.send(json.dumps({"type": "announce", "utt_id": utt_id, "clip_start_ms": start}))
        line.queue(clip.astype("<i2").tobytes())

    async def _babble_source(self) -> Callable[[int], np.ndarray]:
        """Babble noise is mixed from other Twin voices (C11 step 4)."""
        if self._babble is None:
            parts = [await self.synth.synth(TtsRequest(text="haan boliye batao kitna hua theek hai na", lang="hi", voice=name, rate=r))
                     for name, r in (("Kavya", 1.1), ("Arjun", 0.95), ("Nalini", 1.2))]
            self._babble = np.concatenate([np.frombuffer(p.pcm_s16le, "<i2").astype(np.float64) for p in parts])
        babble = self._babble
        return lambda m: np.resize(babble, m)

    def _gold(self, plan: CallPlan, tags: Sequence[str]) -> GoldLabel:
        s, v = plan.script, plan.voice
        hard = ["hard"] if plan.channel.snr_db < 8 and "hard" not in s.tags else []
        return GoldLabel(call_id=plan.call_id, script_id=s.script_id, split=plan.split, scenario=plan.scenario, gold_intent=s.intent,  # type: ignore[arg-type]
                         gold_slots={k: s.gold[k] for k in ("party", "amount", "date") if k in s.gold}, spoken=[t.w.lower() for t in s.tokens],
                         tags=[*s.tags, *hard, *tags], seed=plan.seed, ts=time.time(),
                         channel_params={**asdict(plan.channel), "voice": v.name, "rate": v.rate, "pitch_st": v.pitch_st, "caller": self.params.stamp})


class Driver:
    """Streams, batches and canaries (K08, K28, K33), under the credit governor."""

    def __init__(self, runner: TwinRunner, accounts: Mapping[str, Sequence[AccountSpec]], governor: Governor, canaries: Canaries, *,
                 stats: StatsSource | None = None, concurrency: int = 4, stream_rate_per_min: float = 6.0, seed: int = 0,
                 templates: Mapping[tuple[str, str], Sequence[str]] = CARRIERS) -> None:
        self.runner, self.accounts, self.governor, self.canaries, self.stats, self.concurrency = runner, accounts, governor, canaries, stats, concurrency
        self.stream_rate, self.templates, self.rng = stream_rate_per_min, templates, random.Random(seed)
        self.slots = asyncio.Semaphore(concurrency)
        self.filling = False
        self.scenario: str | None = None  # the running stream
        self.rate = 0.0
        self.counts: Counter[str] = Counter()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._deficit: dict[str, int] = {}  # cells under recertification and the Calib calls they still need (K15)
        self._collecting = 0

    async def one(self, scenario: str, *, split: str | None = None, cell: str | None = None, canary: bool = False, collect: bool = False) -> bool:
        async with self.slots:
            plan = plan_call(self.rng, SCENARIOS[scenario], self.accounts, self.runner.today(), cell=cell, split=split, templates=self.templates)
            self.counts["started"] += 1
            try:
                used = await self.runner.run_call(plan, ["canary"] if canary else [], collect=collect)
            except Exception:  # a call boundary: whatever went wrong inside it must not end the batch
                log.exception("call %s failed", plan.call_id)
                used = None
        if used is None:
            self.counts["failed"] += 1
            return False
        self.governor.charge(used)
        self.counts["completed"] += 1
        return True

    def _spawn(self, scenario: str, **kw: Any) -> None:
        task = asyncio.create_task(self.one(scenario, **kw))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def batch(self, scenario: str, n: int, *, split: str | None = None, cell: str | None = None) -> Counter[str]:
        """n calls of one scenario, `concurrency` at a time (K33)."""
        done: Counter[str] = Counter()
        for ok in await asyncio.gather(*(self.one(scenario, split=split, cell=cell) for _ in range(n))):
            done["completed" if ok else "failed"] += 1
        return done

    async def fill(self, targets: Mapping[str, int], *, calib: bool = False, scenario: str = "S0") -> Counter[str]:
        """Calls until every split has its target (K33). With `calib`, the Calib calls follow the cell deficits of C14 (K15), not the RTP proportions."""
        produced: Counter[str] = Counter()
        failed = 0
        self.filling = True
        try:
            while (nxt := self.governor.next_call(await self.stats() if calib and self.stats else None, produced, targets)) is not None:
                split, cell = nxt
                k = min(self.concurrency, targets[split] - produced[split])
                results = await asyncio.gather(*(self.one(scenario, split=split, cell=cell, collect=True) for _ in range(k)))
                produced[split] += sum(results)
                failed = 0 if any(results) else failed + 1
                if failed >= 20:
                    raise RuntimeError("20 batches in a row without a completed call: is the gateway up?")
        finally:
            self.filling = False
        return produced

    async def command(self, cmd: ScenarioCommand) -> dict[str, Any]:
        p = cmd.params
        if p.speed:
            self.runner.speed = p.speed
        if p.seed is not None:
            self.rng.seed(p.seed)
        if cmd.action == "STOP":
            self.scenario, self.rate = None, 0.0
            for t in list(self._tasks):
                t.cancel()
        elif p.n_calls:
            self._spawn_batch(cmd)
        else:  # START and INJECT both set what the stream plays now; INJECT on an idle Twin starts it
            self.scenario, self.rate = cmd.scenario, p.rate if p.rate is not None else self.rate or self.stream_rate
        return self.status()

    def _spawn_batch(self, cmd: ScenarioCommand) -> None:
        task = asyncio.create_task(self.batch(cmd.scenario, cmd.params.n_calls or 0, split=cmd.params.split, cell=cmd.params.cell))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start_fill(self, targets: Mapping[str, int], *, calib: bool, scenario: str) -> None:
        task = asyncio.create_task(self.fill(targets, calib=calib, scenario=scenario))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda t: t.cancelled() or t.exception() is None or log.error("fill failed: %s", t.exception()))

    def canary(self, req: CanaryRequest) -> None:
        self.canaries.request(req, time.monotonic())

    async def _recertify(self) -> None:
        """D12 step 4: the sentinel has raised the canary of a cell, so the cell is under recertification; the Calib calls it still needs (K15) are collected
        under the scenario that is playing now. Calls are COLLECT calls: the live traffic of the other cells is not touched."""
        stats = await self.stats() if self.stats else None
        self._deficit = {c: s.deficit_by_rung[0] for c, s in stats.cells.items() if s.deficit_by_rung[0] > 0 and c in self.canaries.until} if stats else {}

    def _spawn_collect(self, cell: str) -> None:
        async def go() -> None:
            try:
                await self.one(self.scenario or "S0", split="Calib", cell=cell, collect=True)
            finally:
                self._collecting -= 1
        self._collecting += 1  # counted now, not when the task first runs, or one tick could start them all
        task = asyncio.create_task(go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def status(self) -> dict[str, Any]:
        return {"running": self.scenario is not None, "filling": self.filling, "scenario": self.scenario, "rate_per_min": self.rate, "speed": self.runner.speed,
                "in_flight": len(self._tasks), **self.counts, "credits": {"spent": self.governor.spent, "usable": self.governor.usable, "per_call": self.governor.cost},
                "canary_rate": dict(self.canaries.rate)}

    async def run(self) -> None:
        """The scheduler: stream calls at the configured rate, plus canaries while a stream runs or a sentinel asked for them. Runs until cancelled."""
        tick, last, owed, refreshed = 0.25, time.monotonic(), 0.0, 0.0
        while True:
            await asyncio.sleep(tick)
            now = time.monotonic()
            dt, last = now - last, now
            if self.scenario and now - refreshed >= 1.0:
                refreshed = now
                await self._recertify()
            while self._deficit and self._collecting < max(1, self.concurrency // 2) and self.governor.can_afford():
                cell = max(self._deficit, key=lambda c: (self._deficit[c], c))
                self._deficit[cell] -= 1
                if not self._deficit[cell]:
                    del self._deficit[cell]
                self._spawn_collect(cell)
            if self.scenario and self.governor.can_afford():
                owed += self.rate / 60 * dt
                while owed >= 1.0:
                    owed -= 1.0
                    self._spawn(self.scenario)
            raised = bool(self.canaries.until)
            if self.scenario or raised:
                for cell in self.canaries.due(now, dt, cap=None if self.governor.can_afford() else 0.0):
                    self._spawn(self.scenario or "S0", cell=cell, split=self.rng.choice(["Train", "Valid", "Calib"]), canary=True)


def create_app(driver: Driver) -> FastAPI:
    """Control API of the Twin process: ScenarioCommand from the console (K08) and the benchmark (K33), CanaryRequest from the sentinel (K28)."""
    app = FastAPI(title="Lexi Twin")

    @app.post("/command")
    async def command(cmd: ScenarioCommand) -> dict[str, Any]:
        return await driver.command(cmd)

    @app.post("/fill")
    async def fill(body: dict[str, Any]) -> dict[str, Any]:
        driver.start_fill(body["targets"], calib=body.get("calib", False), scenario=body.get("scenario", "S0"))
        return driver.status()

    @app.post("/canary")
    async def canary(req: CanaryRequest) -> dict[str, bool]:
        driver.canary(req)
        return {"ok": True}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return driver.status()

    return app
