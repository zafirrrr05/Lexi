"""Timbre TTS adapter (13). `sim` is a deterministic formant synthesiser so the demo runs offline (docs/DECISIONS.md D-001);
`live` is Gnani Timbre v2.5 over SSE. 8 kHz s16le out; responses cached by (text, voice, rate, pitch, lang)."""

import base64
import json
import os
import re
from typing import Any

import httpx
import numpy as np
from scipy.signal import lfilter

from lexi.adapters.cache import ProviderCache
from lexi.common.errors import ProviderError
from lexi.common.hashing import sha256_hex
from lexi.common.types import TtsAudio, TtsRequest

SR = 8000
PEAK = 0.5 * 32767
SYLLABLE_S = 0.17  # per syllable at rate 1.0
GAP_S = 0.06  # between words
VOWELS = {"a": (730, 1090, 2440), "i": (270, 2290, 3010), "u": (300, 870, 2240), "e": (530, 1840, 2480), "o": (570, 840, 2410)}
BANDWIDTHS = (70, 100, 140)
_PAUSE = re.compile(r"^<p(\d+)>$")
_INDIC_LETTERS = re.compile("[अ-औक-हஅ-ஔக-ஹ]")


def syllables(token: str) -> int:
    """Vowel nuclei in a Latin token; letters that are not followed by a virama in Devanagari or Tamil; one per digit."""
    n = len(re.findall(r"[aeiouy]+", token.lower())) + sum(c.isdigit() for c in token)
    n += sum(1 for i, c in enumerate(token) if _INDIC_LETTERS.match(c) and token[i + 1:i + 2] not in ("्", "்"))
    return max(n, 1)


def _resonator(x: np.ndarray, freq: float, bw: float) -> np.ndarray:
    r = np.exp(-np.pi * bw / SR)
    c1, c2 = 2 * r * np.cos(2 * np.pi * freq / SR), -r * r
    return np.asarray(lfilter([1 - c1 - c2], [1, -c1, -c2], x))


def _syllable(rng: np.random.Generator, f0: float, fscale: float, vowel: str, dur: float, consonant: bool) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = f0 * (1 + 0.03 * np.sin(2 * np.pi * 5 * t)) * (1.04 - 0.08 * t / max(dur, 1e-3))  # vibrato and declination
    phase = np.cumsum(f / SR)
    src = np.diff(np.floor(phase), prepend=0.0).clip(0, 1) * 1.0  # glottal pulse train
    src = lfilter([1.0], [1.0, -0.95], src)  # glottal spectral tilt
    y = src + 0.04 * rng.standard_normal(n)  # breathiness
    for formant, bw in zip(VOWELS[vowel], BANDWIDTHS, strict=True):
        y = _resonator(y, formant * fscale, bw)
    y = y / (np.abs(y).max() + 1e-9) * np.sin(np.pi * np.arange(n) / n) ** 0.7  # syllabic amplitude modulation
    if consonant:  # voiceless burst before the vowel
        m = int(0.03 * SR)
        burst = lfilter([1, -0.9], [1], rng.standard_normal(m)) * np.hanning(m) * 0.25
        y[:m] += burst / (np.abs(burst).max() + 1e-9) * 0.25
    return np.asarray(y)


def synth_sim(req: TtsRequest) -> tuple[np.ndarray, list[tuple[str, float, float]]]:
    h = int(sha256_hex(req.voice)[:8], 16)
    f0 = (105 + h % 120) * 2 ** (req.pitch_semitones / 12)
    fscale = 0.92 + (h >> 8) % 24 / 100
    rng = np.random.default_rng(int(sha256_hex(f"{req.text}|{req.voice}|{req.rate}|{req.pitch_semitones}")[:12], 16))
    out: list[np.ndarray] = []
    timings: list[tuple[str, float, float]] = []
    t = 0.0
    for tok in req.text.split():
        if (m := _PAUSE.match(tok)):
            out.append(np.zeros(int(int(m.group(1)) / 1000 * SR)))
            t += int(m.group(1)) / 1000
            continue
        pieces = []
        keys = sha256_hex(tok)
        for s in range(syllables(tok)):
            vowel = "aieou"[int(keys[s % 32], 16) % 5]
            pieces.append(_syllable(rng, f0, fscale, vowel, SYLLABLE_S / max(req.rate, 0.1) * (1.2 if s == syllables(tok) - 1 else 1.0), int(keys[-1 - s % 31], 16) % 3 > 0))
        word = np.concatenate(pieces)
        dur = len(word) / SR
        timings.append((tok, t, t + dur))
        out += [word, np.zeros(int(GAP_S * SR))]
        t += dur + GAP_S
    pcm = np.concatenate(out) if out else np.zeros(0)
    return pcm / (np.abs(pcm).max() + 1e-9) * PEAK if len(pcm) else pcm, timings


def _pack(pcm: bytes, timings: list[tuple[str, float, float]] | None) -> bytes:
    return json.dumps(timings).encode() + b"\n" + pcm


def _unpack(blob: bytes) -> tuple[bytes, list[tuple[str, float, float]] | None]:
    head, pcm = blob.split(b"\n", 1)
    t = json.loads(head)
    return pcm, [(a, b, c) for a, b, c in t] if t else None


class TimbreClient:
    def __init__(self, mode: str, cfg: dict[str, Any], cache: ProviderCache | None) -> None:
        if mode not in ("sim", "live"):
            raise ValueError(f"timbre mode must be sim or live, got {mode!r}")
        self.mode, self.cfg, self.cache = mode, cfg, cache

    async def synth(self, req: TtsRequest) -> TtsAudio:
        key = sha256_hex(f"{req.text}|{req.voice}|{req.rate}|{req.pitch_semitones}|{req.lang}")
        if self.cache and self.cache.serves_hits and (blob := self.cache.get("timbre", key)):
            pcm, timings = _unpack(blob)
            return TtsAudio(pcm_s16le=pcm, sample_rate=SR, duration_ms=len(pcm) / 2 / SR * 1000, voice=req.voice, cached=True, word_timings=timings)
        if self.cache and self.cache.mode == "replay":
            raise ProviderError("timbre: replay cache miss")
        if self.mode == "sim":
            x, timings = synth_sim(req)
            pcm = np.round(x).astype("<i2").tobytes()
        else:
            pcm, timings = await self._live(req), None
        if self.cache and self.cache.stores:
            self.cache.put("timbre", key, _pack(pcm, timings))
        return TtsAudio(pcm_s16le=pcm, sample_rate=SR, duration_ms=len(pcm) / 2 / SR * 1000, voice=req.voice, cached=False, word_timings=timings)

    async def _live(self, req: TtsRequest) -> bytes:
        """Gnani Timbre v2.5 SSE: events start, chunk (base64 audio), complete, error. No retry on the hot path; no pitch control."""
        body = {"text": re.sub(r"<p\d+>", "", req.text), "model": self.cfg["model"], "voice": req.voice,
                "language": {"hi": "hi-IN", "ta": "ta-IN"}[req.lang], "speed": min(1.15, max(0.85, req.rate)),
                "audio_config": {"sample_rate": SR, "num_channels": 1, "sample_width": 2, "encoding": "pcm_s16le", "container": "raw"}}
        headers = {"X-API-Key-ID": os.environ.get(self.cfg["key_env"], "")}
        pcm, event = bytearray(), ""
        try:
            async with httpx.AsyncClient(timeout=self.cfg.get("timeout_ms", 1500) / 1000) as http:
                async with http.stream("POST", self.cfg["url"], headers=headers, json=body) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line[5:])
                            if event == "error":
                                raise ProviderError(f"timbre: {data}")
                            if event == "chunk":
                                pcm += base64.b64decode(data["audio"])
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise ProviderError(f"timbre: {e}") from e
        if not pcm:
            raise ProviderError("timbre: empty audio")
        return bytes(pcm)
