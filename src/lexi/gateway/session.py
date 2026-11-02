"""One call (C1): frames in, turns out, speech back. The session owns the audio clock: every timeout counts 20 ms frames, never wall time."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from lexi.adapters.prisma import PrismaClient
from lexi.common.clock import Clock
from lexi.common.errors import LedgerError
from lexi.common.logging import get_logger, observe
from lexi.common.types import Asr, CallControl, Expects, LedgerSignal, LedgerView, SpeechPlan, TurnInput, Word
from lexi.gateway.dialogue import DialogueState
from lexi.gateway.endpointing import Endpointer, Utterance
from lexi.gateway.prisma_client import AsrStream
from lexi.gateway.tts_player import Clip, TtsFailed, TtsPlayer
from lexi.profiler.profile import Profiler

log = get_logger("gateway")
FRAME_BYTES, FRAME_MS = 320, 20


@dataclass
class Ports:
    """What the composition root wires in. The gateway imports none of its downstream components (TP1)."""

    cfg: dict[str, Any]
    clock: Clock
    profiler: Callable[[], Profiler]
    prisma: PrismaClient
    tts: TtsPlayer
    open_tail: Callable[[Sequence[Word], str], bool]
    on_turn: Callable[[TurnInput], Awaitable[None]]  # H05
    signal: Callable[[LedgerSignal], Awaitable[LedgerView | None]]  # H19; SESSION_OPEN answers with the view (H17)
    on_control: Callable[[CallControl], None]  # H23


class Session:
    def __init__(self, ws: WebSocket, session_id: str, account: str, lang: str, softphone: bool, ports: Ports, *, collect: bool = False) -> None:
        self.ws, self.id, self.account, self.lang, self.softphone, self.p, self.collect = ws, session_id, account, lang, softphone, ports, collect
        self.profiler = ports.profiler()
        self.asr = AsrStream(ports.prisma, session_id, lang)
        self.ep = Endpointer(ports.cfg, lang, ports.open_tail)
        self.state = DialogueState()
        self.view: LedgerView
        self.now_ms = 0
        self.play_until = 0  # audio time at which the system stops talking
        self.playing = False
        self.barge_in_ok = True
        self.voiced_ms = 0  # caller speech heard during playback, for barge-in
        self.receipt_id: str | None = None  # entry whose receipt is being (or was) spoken
        self.thinking = False  # a turn is in the pipeline: no endpointing until the plan arrives
        self.closing = False  # the plan was the last: close when it has been played
        self.closed = False
        self.turns = 0
        self._tasks: set[asyncio.Task[None]] = set()

    # ---- life cycle
    async def open(self) -> None:
        view = await self.p.signal(LedgerSignal(trace_id=self.id, ts=self.p.clock.now(), type="SESSION_OPEN", session_id=self.id, account_id=self.account))
        if view is None:
            raise LedgerError("SESSION_OPEN returned no view")
        self.view = view
        await self.asr.start()
        await self._control(f"{self.id}-open", Expects(kind="NEW_COMMAND"), "", 0.0)  # tells the caller it may speak (D-002)

    async def run(self) -> None:
        try:
            while not self.closed:
                msg = await self.ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await self._audio(msg["bytes"])
                elif msg.get("text"):
                    self._text(msg["text"])
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for t in list(self._tasks):
            t.cancel()
        try:
            await self.p.signal(LedgerSignal(trace_id=self.id, ts=self.p.clock.now(), type="SESSION_CLOSE", session_id=self.id))
        except LedgerError as e:
            log.warning("session %s: close signal failed: %s", self.id, e)
        await self.asr.close()
        try:
            await self.ws.close(1000)
        except (RuntimeError, WebSocketDisconnect):
            pass  # the caller hung up first

    # ---- audio in
    def _text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if msg.get("type") == "announce":
            self.asr.announce(int(msg["utt_id"]), int(msg["clip_start_ms"]))

    async def _audio(self, data: bytes) -> None:
        for i in range(0, len(data) - FRAME_BYTES + 1, FRAME_BYTES):
            await self._frame(data[i:i + FRAME_BYTES])

    async def _frame(self, frame: bytes) -> None:
        speech = self.profiler.feed(frame)[0]
        await self.asr.feed(frame, self.now_ms)
        for ev in await self.asr.poll():
            self.ep.on_event(ev)
        self.now_ms += FRAME_MS
        if self.closing:
            if self.now_ms >= self.play_until:
                await self.close()
            return
        if self.thinking:
            return
        if self.playing:
            self.voiced_ms = self.voiced_ms + FRAME_MS if speech else 0
            if self.now_ms < self.play_until and not (self.barge_in_ok and self.voiced_ms >= self.p.cfg["barge_in_ms"]):
                return
            await self._playback_over(barge_in=self.now_ms < self.play_until)
        utt = self.ep.on_frame(self.now_ms, speech, self.profiler.trailing_silence_ms)
        if utt:
            await self._emit(utt)

    # ---- turns
    async def _emit(self, utt: Utterance) -> None:
        t0 = time.perf_counter()
        turn_id = f"{self.id}-T{self.turns}"
        self.turns += 1
        asr = Asr(words=utt.words, text=" ".join(w.w for w in utt.words), lang=utt.lang, asr_status=utt.status)
        now = self.p.clock.now()
        turn = TurnInput(trace_id=turn_id, ts=now, session_id=self.id, turn_id=turn_id, asr=asr, profile=self.profiler.finalize(turn_id),
                         dialogue=self.state.on_turn(), ledger_view=self.view, clock_ts=now, long_utterance=utt.long, collect=self.collect)
        self.ep.reset(self.now_ms)
        self.asr.turn_done()
        self.thinking = True
        observe("gateway_overhead_ms", (time.perf_counter() - t0) * 1000)
        task = asyncio.create_task(self._run_turn(turn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_turn(self, turn: TurnInput) -> None:
        try:
            await self.p.on_turn(turn)
        except Exception:
            log.exception("session %s: turn %s failed in the pipeline; nothing was committed for it", self.id, turn.turn_id)
        finally:
            self.thinking = False
            self.ep.listen(max(self.ep.listen_from, self.now_ms))

    def renew(self, turn: TurnInput) -> TurnInput:
        """C8 step 3, CONTINUE: the caller went on after a receipt, so the same words are a new command. Counters restart; the session's entries stay counted."""
        self.state.new_action()
        return turn.model_copy(update={"dialogue": self.state.on_turn()})

    def on_view(self, view: LedgerView) -> None:
        """H17. Once the receipt's entry is no longer pending the undo window is over, and so is the call (one action per call)."""
        self.view = view
        if self.receipt_id and self.state.mode == "UNDO_WINDOW" and not any(e.entry_id == self.receipt_id and e.status == "PENDING" for e in view.pending_entries):
            self.closing = True

    # ---- speech out
    async def speak(self, plan: SpeechPlan) -> None:
        if self.closed:
            return
        failed = False
        try:
            clip = await self.p.tts.render(plan)
        except TtsFailed as e:
            log.warning("session %s: %s", self.id, e)
            clip, failed = Clip("", b"", 0.0), True
        self.state.on_plan(plan.expects)
        self.play_until = self.now_ms + int(clip.duration_ms)
        self.playing, self.voiced_ms, self.barge_in_ok = clip.duration_ms > 0, 0, plan.barge_in_allowed
        self.ep.listen(self.play_until)
        self.closing = plan.expects.kind == "NONE"
        self.receipt_id = plan.expects.receipt_id
        if failed and self.receipt_id:  # D10: a dynamic receipt that could not be spoken is a receipt that was not delivered
            await self.p.signal(LedgerSignal(trace_id=self.id, ts=self.p.clock.now(), type="RECEIPT_FAILED", session_id=self.id, entry_id=self.receipt_id))
            self.receipt_id = None
        await self._control(plan.plan_id, plan.expects, clip.text, clip.duration_ms, clip.pcm)

    async def _playback_over(self, *, barge_in: bool) -> None:
        self.playing = False
        if barge_in:  # C1 step 6: the caller talked over the system, so playback stops and that speech is the answer
            self.play_until = self.now_ms
            self.ep.speech_from = self.now_ms - self.voiced_ms
            if self.softphone:
                await self._send('{"type": "stop"}')
        elif not self.barge_in_ok:  # talk-over that was not allowed to count belongs to no turn
            self.ep.reset(self.now_ms)
        if self.receipt_id and self.state.mode == "UNDO_WINDOW":
            await self.p.signal(LedgerSignal(trace_id=self.id, ts=self.p.clock.now(), type="RECEIPT_DELIVERED", session_id=self.id, entry_id=self.receipt_id))

    async def _control(self, plan_id: str, expects: Expects, text: str, duration_ms: float, pcm: bytes = b"") -> None:
        ctl = CallControl(trace_id=self.id, ts=self.p.clock.now(), session_id=self.id, plan_id=plan_id, expects=expects, text=text, duration_ms=duration_ms)
        await self._send(ctl.model_dump_json())
        if self.softphone and pcm:  # TTS audio goes only to the browser; the Twin hears nothing and takes the duration from the control message
            await self._send(pcm)
        self.p.on_control(ctl)

    async def _send(self, data: str | bytes) -> None:
        try:
            await (self.ws.send_text(data) if isinstance(data, str) else self.ws.send_bytes(data))
        except (WebSocketDisconnect, RuntimeError):
            self.closed = True
