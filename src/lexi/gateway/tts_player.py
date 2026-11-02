"""Speech out (C1 step 6, FAILS): SpeechPlan segments to Timbre, one clip per plan. Every clip is kept in a prompt cache by its exact text, so a
phrase that was spoken before is still available when Timbre is down. Content that was never synthesised (a receipt with a new amount) is not
guessed: the failure is reported, and the caller of this module signals RECEIPT_FAILED (D10)."""

from dataclasses import dataclass

from lexi.adapters.timbre import TimbreClient
from lexi.common.errors import ProviderError
from lexi.common.types import SpeechPlan, TtsRequest

MAX_PROMPTS = 512  # about 16 MB of 8 kHz audio


@dataclass(frozen=True)
class Clip:
    text: str
    pcm: bytes
    duration_ms: float


class TtsFailed(ProviderError):
    """The plan could not be spoken."""


class TtsPlayer:
    def __init__(self, synth: TimbreClient) -> None:
        self._synth = synth
        self._prompts: dict[str, Clip] = {}

    async def render(self, plan: SpeechPlan) -> Clip:
        """The plan as one clip. An empty plan (a receipt that needs no words) is an empty clip."""
        text = " ".join(s.text for s in plan.segments)
        if not plan.segments:
            return Clip("", b"", 0.0)
        try:
            parts = [await self._synth.synth(TtsRequest(text=s.text, lang=s.lang, voice=s.voice, prosody_hint=s.prosody_hint)) for s in plan.segments]
        except ProviderError as e:
            if text in self._prompts:
                return self._prompts[text]
            raise TtsFailed(str(e)) from e
        clip = Clip(text, b"".join(p.pcm_s16le for p in parts), sum(p.duration_ms for p in parts))
        self._prompts[text] = clip
        if len(self._prompts) > MAX_PROMPTS:  # ponytail: oldest-first eviction, an LRU only if hit rate ever matters
            del self._prompts[next(iter(self._prompts))]
        return clip
