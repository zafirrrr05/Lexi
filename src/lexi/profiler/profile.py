"""C2 Channel Profiler: incremental over the same 20 ms frames Prisma gets; `finalize` only reduces per-turn arrays."""

from collections import deque

import numpy as np

from lexi.common.catalog import Catalog
from lexi.common.types import ChannelProfile
from lexi.profiler.bandwidth import hz95, spectra
from lexi.profiler.cqi import bucket
from lexi.profiler.plc import concealed
from lexi.profiler.snr import nonstationarity_db, snr_db
from lexi.profiler.vad import FRAME_BYTES, FRAME_MS, Vad

NOISE_HISTORY = 150  # 3 s of non-speech frames kept across turns, used when a turn has too few of its own
MIN_NOISE_FRAMES = 5
FULL_SCALE = 32768.0
CLIP_LEVEL = 0.98


class Profiler:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._vad = Vad()
        self._rolling: deque[float] = deque(maxlen=NOISE_HISTORY)  # rolling noise floor
        self._reset()

    def _reset(self) -> None:
        self._power: list[float] = []
        self._speech: list[bool] = []
        self._spec = np.zeros(81)
        self._clipped = 0
        self._samples = 0
        self._concealed = 0
        self._last_frame: np.ndarray | None = None
        self._since_speech = 0
        self._started = False

    def feed(self, pcm: bytes) -> list[bool]:
        flags = self._vad.flags(pcm)
        n = len(flags)
        frames = np.frombuffer(pcm[: n * FRAME_BYTES], dtype="<i2").reshape(n, -1) if n else np.zeros((0, 160), dtype=np.int16)
        x = frames.astype(np.float64) / FULL_SCALE
        power = (x ** 2).mean(axis=1) if n else np.zeros(0)
        self._power += power.tolist()
        self._speech += flags
        self._clipped += int((np.abs(x) >= CLIP_LEVEL).sum())
        self._samples += x.size
        if n:
            self._concealed += int(concealed(frames, self._last_frame).sum())
            self._last_frame = frames[-1]
            speech = np.array(flags)
            if speech.any():
                self._spec += spectra(x[speech]).sum(axis=0)
        for f in flags:
            self._since_speech = 0 if f else self._since_speech + FRAME_MS
            self._started = self._started or f
        return flags

    @property
    def trailing_silence_ms(self) -> float:
        return float(self._since_speech)

    @property
    def speech_started(self) -> bool:
        return self._started

    def finalize(self, turn_id: str) -> ChannelProfile:
        power, speech = np.array(self._power), np.array(self._speech, dtype=bool)
        n_speech = int(speech.sum())
        speech_ms = float(n_speech * FRAME_MS)
        noise = power[~speech] if len(power) else power
        if len(noise) < MIN_NOISE_FRAMES:
            noise = np.concatenate([np.array(self._rolling), noise])
        if len(noise) < MIN_NOISE_FRAMES:  # VAD called everything speech (loud babble): the quietest frames are the floor
            noise = power
        if n_speech and len(noise):
            snr, nonstat = snr_db(power[speech], noise), nonstationarity_db(noise)
        else:
            snr, nonstat = 0.0, 0.0
        plc = self._concealed / len(power) if len(power) else 0.0
        bw = hz95(self._spec)
        cqi, short = bucket(self.catalog, snr, plc, speech_ms)
        env = 10 * np.log10(power[speech] + 1e-12) if n_speech else np.zeros(0)
        peaks = int(((env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > env.mean())).sum()) if len(env) > 2 else 0
        profile = ChannelProfile(
            turn_id=turn_id, snr_db=snr, bandwidth_hz95=bw, bandwidth_class="NB" if bw <= self.catalog.wb_above_hz95 else "WB",
            plc_fraction=plc, clip_fraction=self._clipped / self._samples if self._samples else 0.0, noise_nonstat=nonstat,
            speech_ms=speech_ms, rate_proxy=peaks / (speech_ms / 1000) if speech_ms else 0.0, cqi=cqi,
            profiler_version=self.catalog.profiler_version, short_speech=short, trailing_silence_ms=self.trailing_silence_ms)
        self._rolling.extend(power[~speech].tolist())
        self._reset()
        return profile
