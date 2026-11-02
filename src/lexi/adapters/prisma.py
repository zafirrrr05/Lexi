"""Prisma STT adapter (13). `sim` turns the Twin's announced utterance into a noisy streaming transcript so the demo runs
offline (docs/DECISIONS.md D-001); `live` is Gnani Prisma v3 over WebSocket.

Sim error model: per-token substitution probability rises as SNR falls and with packet loss (numerals, scales and names
1.5x), substitutions prefer the token's confusable group, cue words are rarely misheard, deletions are rarer still (and carry no confidence signal), confidences are informative but overlap.
"""

import asyncio
import json
import os
import random
from pathlib import Path
from typing import Any

import websockets
import yaml

from lexi.adapters.cache import ProviderCache
from lexi.common.catalog import CONFIG_DIR
from lexi.common.errors import ProviderError
from lexi.common.types import PrismaStream, Word

FRAME_MS = 20
CHUNK = 1024  # live: 512 samples at 8 kHz
SEGMENT_GAP_MS = 300  # a gap this long closes a segment, as a streaming recogniser would
HARD = {"num", "scale", "frac", "party"}
LEXICON_DIR = CONFIG_DIR / "lexicons"
CREDITS_PER_AUDIO_MINUTE = 1.0
CUE_ERROR = ((18.0, 0.002), (8.0, 0.012), (-99.0, 0.04))  # (SNR floor, probability), 4.6 buckets


def _sub_prob(snr_db: float, plc: float) -> float:
    base = 0.02 if snr_db >= 18 else 0.02 + (18 - snr_db) / 10 * 0.06 if snr_db >= 8 else min(0.08 + (8 - snr_db) / 18 * 0.13, 0.25)
    return min(base + 1.0 * plc, 0.6)


class SimLexicon:
    """Forms of numeric lexemes, confusable groups and cue classes, read straight from the lexicon YAML."""

    def __init__(self, lang: str, directory: Path = LEXICON_DIR) -> None:
        load = lambda name: yaml.safe_load((directory / lang / f"{name}.yaml").read_text(encoding="utf-8"))  # noqa: E731
        self.kind_forms: dict[str, list[dict[str, list[str]]]] = {k: [e["forms"] for e in load(f)] for k, f in
                                                                   (("num", "numerals"), ("scale", "scales"), ("frac", "fractions"))}
        self.by_latin = {f["latin"][0]: f for forms in self.kind_forms.values() for f in forms}
        self.groups = [g for g in load("confusable")["groups"]]
        cues = load("cues")
        self.cue_forms: dict[str, list[dict[str, list[str]]]] = {}
        for c in cues:
            self.cue_forms.setdefault(c["cls"], []).append(c["forms"])


def _render(forms: dict[str, list[str]], script: str) -> str:
    return (forms.get(script) or forms["latin"])[0]


def _perturb(word: str, rng: random.Random) -> str:
    """A misheard name: one character dropped, doubled or swapped."""
    if len(word) < 3:
        return word
    i = rng.randrange(1, len(word) - 1)
    return rng.choice([word[:i] + word[i + 1:], word[:i] + word[i] + word[i:], word[:i] + word[i + 1] + word[i] + word[i + 2:]])


def hypothesize(entry: dict[str, Any], lex: SimLexicon, rng: random.Random, confidences: bool) -> list[tuple[str, float, float, float | None]]:
    """[(word, t0_s, t1_s, conf)] from the Twin's token list and channel. Deterministic given `rng`."""
    ch = entry["channel"]
    snr, plc = ch["snr_db"], ch["plc_fraction"]
    p_sub = _sub_prob(snr, plc)
    p_cue = next(p for floor, p in CUE_ERROR if snr >= floor)
    script = {"hi": "deva", "ta": "tamil"}[entry["lang"]] if rng.random() < 0.6 else "latin"
    out: list[tuple[str, float, float, float | None]] = []
    for tok in entry["tokens"]:
        kind, forms = tok["kind"], tok["forms"]
        p = p_cue if kind in ("cue", "answer") else min(p_sub * (1.5 if kind in HARD else 1.0), 0.8)
        if rng.random() < p * 0.01 and kind not in ("cue", "answer"):
            continue  # deleted
        wrong = rng.random() < p
        text = _render(forms, script)
        if wrong:
            latin = forms["latin"][0]
            group = next((g for g in lex.groups if latin in g), None)
            if kind in ("cue", "answer") and (opp := _opposite(kind, tok, lex, rng)):
                text = opp
            elif group and rng.random() < 0.8 and (others := [m for m in group if m != latin and m in lex.by_latin]):
                text = _render(lex.by_latin[rng.choice(others)], script)
            elif kind in lex.kind_forms:
                text = _render(rng.choice(lex.kind_forms[kind]), script)
            elif kind == "party":
                text = _perturb(text, rng)
        good, bad = (5.0 + max(snr, 0) / 10, 1.2) if snr >= 8 else (5.0, 1.6), (2.5, 3.0) if snr >= 8 else (1.8, 4.0)
        a, b = bad if wrong else good
        out.append((text, tok["t0"], tok["t1"], round(rng.betavariate(a, b), 3) if confidences else None))
        if rng.random() < p_sub * 0.1:  # inserted filler word
            out.append(("haan" if script == "latin" else _render({"latin": ["haan"], "deva": ["हाँ"]}, script), tok["t1"], tok["t1"] + 0.05, 0.3 if confidences else None))
    return out


def _opposite(kind: str, tok: dict[str, Any], lex: SimLexicon, rng: random.Random) -> str | None:
    """Cue words are sometimes heard as the opposite cue (credit <-> debit)."""
    flip = {"CREDIT": "DEBIT", "DEBIT": "CREDIT"}
    for cls, forms in lex.cue_forms.items():
        if any(tok["forms"]["latin"][0] in f["latin"] for f in forms) and cls in flip:
            return rng.choice(lex.cue_forms[flip[cls]])["latin"][0]
    return None


class PrismaSession:
    def __init__(self, session_id: str, lang: str | None, client: "PrismaClient") -> None:
        self.session_id, self.lang, self._c = session_id, lang, client
        self.credits_used = 0.0
        self._audio_ms = 0.0
        self._due: list[tuple[float, int, PrismaStream]] = []  # (release time ms, order, event)
        self._ready: list[PrismaStream] = []
        self._announced: dict[int, int] = {}
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._order = 0

    def announce(self, utt_id: int, clip_start_ms: int) -> None:
        """Sim only: the Twin tells Prisma which clip starts when, so it can derive a hypothesis for it."""
        entry = self._c.cache.get_sim_utterance(utt_id)
        if entry is None:
            raise ProviderError(f"prisma sim: utterance {utt_id} not announced by the Twin")
        rng = random.Random(entry["seed"] * 1_000_003 + utt_id)
        words = [Word(w=w, t0=clip_start_ms + t0 * 1000, t1=clip_start_ms + t1 * 1000, conf=c, lang=entry["lang"])
                 for w, t0, t1, c in hypothesize(entry, self._c.lexicon(entry["lang"]), rng, self._c.confidences)]
        segment: list[Word] = []
        for i, w in enumerate(words):
            segment.append(w)
            self._schedule(w.t1, "partial", segment)
            if i + 1 == len(words) or words[i + 1].t0 - w.t1 >= SEGMENT_GAP_MS:
                self._schedule(w.t1 + SEGMENT_GAP_MS, "final", segment)
                segment = []
        if not words:  # nothing heard: Prisma reports an empty final once the clip is over
            self._schedule(clip_start_ms + max((t["t1"] for t in entry["tokens"]), default=0) * 1000 + SEGMENT_GAP_MS, "final", [], "EMPTY")

    def _schedule(self, at_ms: float, kind: str, words: list[Word], status: str = "OK") -> None:
        ws = list(words)
        ev = PrismaStream(session_id=self.session_id, kind=kind, words=ws, text=" ".join(w.w for w in ws), lang=self.lang or "hi",  # type: ignore[arg-type]
                          t0_ms=int(ws[0].t0) if ws else int(at_ms), t1_ms=int(ws[-1].t1) if ws else int(at_ms), status=status)  # type: ignore[arg-type]
        self._order += 1
        self._due.append((at_ms, self._order, ev))

    async def feed(self, pcm: bytes, ts_ms: int) -> None:
        self.credits_used += len(pcm) / 16000 / 60 * CREDITS_PER_AUDIO_MINUTE
        if self._c.mode == "sim":
            if self._c.fault:
                if not self._ready or self._ready[-1].kind != "error":
                    self._ready.append(PrismaStream(session_id=self.session_id, kind="error", words=[], text="", lang=self.lang or "hi",
                                                    t0_ms=ts_ms, t1_ms=ts_ms, status="UNAVAILABLE" if self._c.fault == "unavailable" else "TIMEOUT"))
                return
            self._audio_ms = ts_ms + len(pcm) / 16
            due = sorted(e for e in self._due if e[0] <= self._audio_ms)
            self._due = [e for e in self._due if e[0] > self._audio_ms]
            self._ready += [ev for _, _, ev in due]
            return
        await self._live_send(pcm)

    def poll(self) -> list[PrismaStream]:
        out, self._ready = self._ready, []
        return out

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws:
            await self._ws.close()

    # ---- live
    async def _connect(self) -> None:
        cfg = self._c.cfg
        headers = {"x-api-key-id": os.environ.get(cfg["key_env"], ""), "lang_code": cfg["lang_code"][self.lang or "hi"],
                   "x-sample-rate": str(cfg["sample_rate"]), "x-format": "verbatim"}
        try:
            self._ws = await websockets.connect(cfg["url"], additional_headers=headers, open_timeout=cfg.get("connect_timeout_s", 3))
        except (OSError, websockets.WebSocketException) as e:
            raise ProviderError(f"prisma: {e}") from e
        self._reader = asyncio.create_task(self._read())
        self._buf = b""
        self._sent_ms = 0.0
        self._seg_start = 0.0

    async def _read(self) -> None:
        """transcript events carry text only (assumption V5): words are spread evenly over the segment, conf=None so C3 raises conf_missing."""
        try:
            async for raw in self._ws:
                ev = json.loads(raw)
                if ev.get("type") == "transcript" or "text" in ev:
                    self._ready.append(self._from_transcript(ev))
        except (websockets.WebSocketException, ValueError):
            self._ready.append(PrismaStream(session_id=self.session_id, kind="error", words=[], text="", lang=self.lang or "hi", t0_ms=0, t1_ms=0, status="UNAVAILABLE"))

    def _from_transcript(self, ev: dict[str, Any]) -> PrismaStream:
        text = ev.get("text", "")
        t0 = self._seg_start
        t1 = t0 + float(ev.get("audio_duration_ms", 0))
        self._seg_start = t1
        listed = ev.get("words")
        if listed:
            words = [Word(w=x.get("word", x.get("w", "")), t0=x.get("start", x.get("t0", t0)), t1=x.get("end", x.get("t1", t1)),
                          conf=x.get("confidence", x.get("conf")), lang=self.lang or "hi") for x in listed]
        else:
            toks = text.split()
            step = (t1 - t0) / max(len(toks), 1)
            words = [Word(w=w, t0=t0 + i * step, t1=t0 + (i + 1) * step, conf=None, lang=self.lang or "hi") for i, w in enumerate(toks)]
        return PrismaStream(session_id=self.session_id, kind="final", words=words, text=text, lang=self.lang or "hi",
                            t0_ms=int(t0), t1_ms=int(t1), status="OK" if text else "EMPTY")

    async def _live_send(self, pcm: bytes) -> None:
        if self._ws is None:
            await self._connect()
        self._buf += pcm
        while len(self._buf) >= CHUNK:
            await self._ws.send(self._buf[:CHUNK])
            self._buf = self._buf[CHUNK:]


class PrismaClient:
    def __init__(self, mode: str, cfg: dict[str, Any], cache: ProviderCache, lexicon_dir: Path | None = None, *,
                 fault: str | None = None, confidences: bool = True) -> None:
        if mode not in ("sim", "live"):
            raise ValueError(f"prisma mode must be sim or live, got {mode!r}")
        self.mode, self.cfg, self.cache, self.fault, self.confidences = mode, cfg, cache, fault, confidences
        self._dir = lexicon_dir or LEXICON_DIR
        self._lex: dict[str, SimLexicon] = {}

    def lexicon(self, lang: str) -> SimLexicon:
        return self._lex.setdefault(lang, SimLexicon(lang, self._dir))

    async def open(self, session_id: str, lang: str | None) -> PrismaSession:
        return PrismaSession(session_id, lang, self)


