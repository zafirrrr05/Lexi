"""Channel simulator (C11 step 4): reverb, noise at a target SNR, clipping, codec, packet loss with concealment."""

import random
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

import numpy as np

from lexi.twin.noise import make_noise

FRAME = 160
ACTIVE_DB = -10.0  # a frame is active speech when within 10 dB of the loudest frame
SPEECH_RMS = 2500.0  # every caller speaks at this level (-22 dBFS), so the noise floor of a call is known before the first word
CLIP = 0.98 * 32768
MU = 255.0


@dataclass(frozen=True)
class ChannelParams:
    codec: str  # none | mulaw | amr
    noise: str  # none | babble | market | traffic | rain
    snr_db: float
    loss_pct: float
    rt60_s: float
    clip: bool


def channel_for_cqi(cqi: str, rng: random.Random) -> ChannelParams:
    """Draw a channel whose measured bucket (4.6) is `cqi`."""
    noise = rng.choice(["none", "babble", "market", "traffic", "rain"])
    if cqi == "Q1":
        snr, loss, codec, rt60 = rng.uniform(20, 35), rng.uniform(0, 0.5), rng.choice(["none", "mulaw"]), rng.uniform(0, 0.3)
    elif cqi == "Q2":
        if rng.random() < 0.5:
            snr, loss = rng.uniform(9, 17), rng.uniform(0, 0.8)
        else:
            snr, loss = rng.uniform(19, 30), rng.uniform(1.2, 2.8)
        codec, rt60 = rng.choice(["mulaw", "amr"]), rng.uniform(0, 0.5)
    elif cqi == "Q3":
        if rng.random() < 0.5:
            snr, loss = rng.uniform(-10, 6), rng.uniform(0, 8)
        else:
            snr, loss = rng.uniform(10, 25), rng.uniform(3.8, 8)
        codec, rt60 = rng.choice(["mulaw", "amr"]), rng.uniform(0.2, 0.6)
    else:
        raise ValueError(cqi)
    return ChannelParams(codec, noise, round(snr, 2), round(loss, 2), round(rt60, 2), cqi == "Q3" and rng.random() < 0.3)


def _reverb(x: np.ndarray, rt60: float, rng: np.random.Generator) -> np.ndarray:
    n = int(rt60 * 8000)
    if n < 16:
        return x
    ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)  # -60 dB at RT60
    ir[0] = 1.0 + np.abs(ir).max()  # direct path dominates
    ir /= np.abs(ir).sum()
    return np.convolve(x, ir)[: len(x)] * np.abs(ir).sum() / max(np.sqrt((ir ** 2).sum()), 1e-9) * 0.7


def _speech_power(x: np.ndarray) -> float:
    """Power over active-speech frames, the same convention C2 measures."""
    f = x[: len(x) // FRAME * FRAME].reshape(-1, FRAME)
    p = (f ** 2).mean(axis=1)
    return float(p[p >= p.max() * 10 ** (ACTIVE_DB / 10)].mean())


def _mulaw(x: np.ndarray) -> np.ndarray:
    y = np.sign(x) * np.log1p(MU * np.minimum(np.abs(x) / 32768, 1.0)) / np.log1p(MU)
    q = np.round(y * 127) / 127  # 8-bit companded
    return np.sign(q) * ((1 + MU) ** np.abs(q) - 1) / MU * 32768


@cache
def amr_available() -> bool:
    return bool(shutil.which("ffmpeg")) and b"libopencore_amrnb" in subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, check=False).stdout


def _amr(x: np.ndarray) -> np.ndarray:
    """AMR-NB 7.95 kbit/s round trip through ffmpeg."""
    enc = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", "8000", "-ac", "1", "-i", "-", "-c:a", "libopencore_amrnb",
                          "-b:a", "7.95k", "-f", "amr", "-"], input=x.astype("<i2").tobytes(), capture_output=True, check=True)
    dec = subprocess.run(["ffmpeg", "-v", "error", "-f", "amr", "-i", "-", "-f", "s16le", "-ar", "8000", "-ac", "1", "-"],
                         input=enc.stdout, capture_output=True, check=True)
    return np.pad(np.frombuffer(dec.stdout, dtype="<i2").astype(np.float64), (0, len(x)))[: len(x)]


def _lose(x: np.ndarray, pct: float, rng: np.random.Generator) -> np.ndarray:
    """Whole 20 ms packets vanish; concealment is silence or a repeat of the previous packet (what C2's PLC detector looks for)."""
    y = x.copy()
    for i in range(1, len(x) // FRAME):
        if rng.random() < pct / 100:
            y[i * FRAME:(i + 1) * FRAME] = 0.0 if rng.random() < 0.5 else y[(i - 1) * FRAME:i * FRAME]
    return y


def apply_channel(pcm: np.ndarray, params: ChannelParams, rng: np.random.Generator,
                  babble_source: Callable[[int], np.ndarray] | None = None) -> np.ndarray:
    x = pcm.astype(np.float64)
    if params.rt60_s > 0:
        x = _reverb(x, params.rt60_s, rng)
    x = x * SPEECH_RMS / np.sqrt(max(_speech_power(x), 1e-9))
    x = x + make_noise(params.noise, len(x), rng, babble_source) * SPEECH_RMS / 10 ** (params.snr_db / 20)
    if params.clip:
        x = x * (CLIP * 1.6 / max(np.abs(x).max(), 1.0))
    x = np.clip(x, -CLIP, CLIP)
    if params.codec == "amr" and amr_available():
        x = _amr(x)
    elif params.codec in ("mulaw", "amr"):  # no AMR encoder installed: the narrowband G.711 stand-in
        x = _mulaw(x)
    if params.loss_pct > 0:
        x = _lose(x, params.loss_pct, rng)
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


class LineNoise:
    """What the line carries when nobody speaks: the call's noise at the call's level, through the codec. A phone line is never digitally silent,
    and exact zeros would read as lost packets (C2) with an infinite SNR."""

    LOOP = 8 * 8000  # ponytail: eight seconds drawn once per call and repeated; the profiler cannot tell, a fresh draw per frame costs more than the call

    def __init__(self, params: ChannelParams, rng: np.random.Generator, babble_source: Callable[[int], np.ndarray] | None = None) -> None:
        x = make_noise(params.noise, self.LOOP, rng, babble_source) * SPEECH_RMS / 10 ** (params.snr_db / 20)
        if params.codec != "none":  # AMR is stateful and file based; idle noise takes the mu-law stand-in
            x = _mulaw(x)
        self._loop = np.clip(np.round(x), -32768, 32767).astype("<i2")
        self._pos = 0

    def take(self, n: int) -> bytes:
        out = np.take(self._loop, np.arange(self._pos, self._pos + n), mode="wrap")
        self._pos = (self._pos + n) % self.LOOP
        return out.tobytes()
