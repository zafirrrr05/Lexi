"""End-of-utterance detection (C1 steps 3, 9). Frame driven, so every timeout is in audio time and a Twin running faster than the wall clock
behaves like a caller on a real line.

EOU = a Prisma final covers the words AND the line has been silent for `eou_silence_ms`. If the last word is a numeral, a scale word or a
connector the wait grows by `numeric_hold_ms` (at most `numeric_hold_max_ms` per turn): "pachaas hazaar ... paanch sau" stays one turn."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lexi.common.types import AsrStatus, PrismaStream, Word

QUIET_EXTRA_MS = 650  # webrtcvad never reports silence on a noisy line (docs/DECISIONS.md D-007): the stream's own quiet, this much longer, stands in
FINAL_TIMEOUT_MS = 2500  # runtime.yaml timeouts_ms.prisma_final: silence has begun but Prisma has not closed the segment


@dataclass(frozen=True)
class Utterance:
    words: list[Word]
    lang: str
    status: AsrStatus
    long: bool = False


class Endpointer:
    def __init__(self, cfg: Mapping[str, Any], lang: str, open_tail: Callable[[Sequence[Word], str], bool]) -> None:
        self.cfg, self.lang, self.open_tail = cfg, lang, open_tail
        self.listen_from = 0
        self.reset(0)

    def reset(self, now_ms: int) -> None:
        """Start a new turn. Words that ended before `now_ms` belong to the turn that just finished."""
        self.floor_ms = self.listen_from = now_ms
        self.speech_from: int | None = None
        self.events: list[PrismaStream] = []
        self.held_ms = 0
        self.silence_peak = 0.0  # longest VAD silence since the caller began: if it never reaches the EOU rule the VAD is not working on this line

    def listen(self, now_ms: int) -> None:
        """The system stopped talking: the no-speech clock starts here."""
        self.listen_from = now_ms

    def on_event(self, ev: PrismaStream) -> None:
        if ev.kind == "error" or ev.t1_ms > self.floor_ms:
            self.events.append(ev)

    def words(self) -> list[Word]:
        """Closed segments, then the segment still being recognised (partials are cumulative, so only the latest counts)."""
        out = [w for e in self.events if e.kind == "final" for w in e.words]
        return out + (self.events[-1].words if self.events and self.events[-1].kind == "partial" else [])

    def on_frame(self, now_ms: int, speech: bool, vad_silence_ms: float) -> Utterance | None:
        cfg = self.cfg
        if speech and self.speech_from is None:
            self.speech_from = now_ms
        if self.speech_from is not None:
            self.silence_peak = max(self.silence_peak, vad_silence_ms)
        words = self.words()
        failed = next((e.status for e in self.events if e.kind == "error"), None)
        started = self.listen_from if self.speech_from is None else self.speech_from
        if self.speech_from is not None and now_ms - self.speech_from >= cfg["max_utterance_s"] * 1000:
            return Utterance(words, self.lang, "OK" if words else "EMPTY", long=True)
        if not self.events:
            return Utterance([], self.lang, "SILENCE") if now_ms - started >= cfg["no_speech_timeout_s"] * 1000 else None
        last = self.events[-1]
        quiet = now_ms - max((w.t1 for w in words), default=last.t1_ms)
        base, hold = cfg["eou_silence_ms"], 0
        if failed is None and words and cfg["numeric_hold"] and self.held_ms < cfg["numeric_hold_max_ms"] and self.open_tail(words, self.lang):
            hold = cfg["numeric_hold_ms"]
        blind = self.silence_peak < base
        silent = vad_silence_ms >= base + hold or (blind and quiet >= base + hold + QUIET_EXTRA_MS)
        if hold and not silent and (vad_silence_ms >= base or (blind and quiet >= base + QUIET_EXTRA_MS)):
            self.held_ms += cfg["frame_ms"]  # waiting beyond the plain rule: counts against the per-turn cap
        if not silent:
            return None
        if failed is not None:
            return Utterance([], self.lang, failed)
        if last.kind == "partial":  # silence has begun but the recogniser has not closed the segment
            return Utterance(words, self.lang, "TIMEOUT") if quiet >= FINAL_TIMEOUT_MS else None
        return Utterance(words, self.lang, "OK" if words else "EMPTY")
