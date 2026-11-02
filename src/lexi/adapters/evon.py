"""Evon LLM adapter (13). `sim` is a rule-based id selector over the C4 prompt format (and a paraphrase stub) so the demo
runs offline (docs/DECISIONS.md D-001); `live` is any OpenAI-compatible chat-completions endpoint. Never raises."""

import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from lexi.adapters.cache import ProviderCache
from lexi.common.catalog import CONFIG_DIR
from lexi.common.hashing import sha256_hex
from lexi.common.types import EvonRequest, EvonResponse

LEXICON_DIR = CONFIG_DIR / "lexicons"
MODEL = "evon-sim-1"
INVALID_JSON = 0.03
WRONG_ID = 0.02
LATENCY_MS = (250, 600)
_TAG = re.compile(r"\[(E\d+):(party|amount|date)\|([^\]]*)\]")
_TAGGED_TEXT = re.compile(r"^TEXT: (.*)$", re.M)
_REQUIRED = re.compile(r"^REQUIRED_SLOTS: (.*)$", re.M)
FILLERS = {"latin": ("ek minute, ", "accha, ", "dekho, "), "tamil": ("ஒரு நிமிடம், ", "சரி, ")}


class SimEvon:
    def __init__(self, lexicon_dir: Path) -> None:
        self.cues: dict[str, tuple[float, str]] = {}  # cue word (latin or script form) -> (strength, class)
        for lang in sorted(p.name for p in lexicon_dir.iterdir() if p.is_dir()):
            for c in yaml.safe_load((lexicon_dir / lang / "cues.yaml").read_text(encoding="utf-8")):
                for forms in c["forms"].values():
                    for f in forms:
                        self.cues[f.lower()] = (float(c["strength"]), c["cls"])

    def parse(self, prompt: str, rng: random.Random) -> str:
        text = _TAGGED_TEXT.search(prompt).group(1)  # type: ignore[union-attr]
        required = {kv.split("=")[0]: kv.split("=")[1].split(",") for kv in _REQUIRED.search(prompt).group(1).split("; ")}  # type: ignore[union-attr]
        tags = [(m.group(1), m.group(2)) for m in _TAG.finditer(text)]
        plain = _TAG.sub(" ", text).lower().split()
        found = [(i, *self.cues[w]) for i, w in enumerate(plain) if w in self.cues]  # (position, strength, class)
        strong = [f for f in found if f[1] >= 0.8] or found
        classes = {"CREDIT": "CREDIT_ENTRY", "DEBIT": "DEBIT_ENTRY", "PROMISE": "PROMISE_TO_PAY", "RESCHEDULE": "RESCHEDULE_DUE"}
        if strong:  # the speaker's last strong cue wins
            intent = classes[max(strong, key=lambda f: (f[0], f[1]))[2]] if any(f[2] in classes for f in strong) else None
        else:
            intent = None
        if intent is None:
            has_date = any(t == "date" for _, t in tags)
            intent = ("PROMISE_TO_PAY" if any(t == "amount" for _, t in tags) else "RESCHEDULE_DUE") if has_date else "CREDIT_ENTRY"
        slots: dict[str, str] = {}
        for slot in required[intent]:
            ids = [i for i, t in tags if t == slot]
            if ids:
                slots[slot] = ids[-1] if slot == "amount" else ids[0]
        if rng.random() < WRONG_ID:  # valid JSON, wrong content
            others = [i for i, t in tags if t == "amount" and slots.get("amount") != i]
            if others and "amount" in slots:
                slots["amount"] = others[0]
            else:
                intent = next(i for i in required if i != intent)
        return json.dumps({"intent": intent, "slots": slots})

    @staticmethod
    def paraphrase(prompt: str, rng: random.Random) -> str:
        template = _TAGGED_TEXT.search(prompt).group(1)  # type: ignore[union-attr]
        if rng.random() < 0.4:
            return template
        pool = FILLERS["tamil" if re.search("[஀-௿]", template) else "latin"]
        return rng.choice(pool) + template


class EvonClient:
    def __init__(self, mode: str, cfg: dict[str, Any], cache: ProviderCache, lexicon_dir: Path | None = None) -> None:
        if mode not in ("sim", "live"):
            raise ValueError(f"evon mode must be sim or live, got {mode!r}")
        self.mode, self.cfg, self.cache = mode, cfg, cache
        self._sim = SimEvon(lexicon_dir or LEXICON_DIR) if mode == "sim" else None

    async def complete(self, req: EvonRequest) -> EvonResponse:
        key = sha256_hex(f"{req.purpose}|{req.prompt}|{req.max_tokens}|{req.temperature}")
        if self.mode == "live" and self.cache.serves_hits and (hit := self.cache.get("evon", key)):
            return EvonResponse(status="OK", text=hit.decode(), latency_ms=0.0, model_version=self.cfg.get("model", "live"))
        if self.mode == "live" and self.cache.mode == "replay":
            return EvonResponse(status="ERROR", text=None, latency_ms=0.0, model_version="replay-miss")
        if self._sim is not None:
            return self._simulate(req, key)
        t0 = time.perf_counter()
        try:
            body = {"model": self.cfg["model"], "messages": [{"role": "user", "content": req.prompt}], "max_tokens": req.max_tokens,
                    "temperature": req.temperature, "chat_template_kwargs": {"enable_thinking": False}}
            headers = {"Authorization": f"Bearer {os.environ[self.cfg['key_env']]}"} if os.environ.get(self.cfg.get("key_env", "")) else {}
            async with httpx.AsyncClient(timeout=req.timeout_ms / 1000) as http:
                resp = await http.post(self.cfg["url"], json=body, headers=headers)
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException:
            return EvonResponse(status="TIMEOUT", text=None, latency_ms=(time.perf_counter() - t0) * 1000, model_version=self.cfg["model"])
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            return EvonResponse(status="ERROR", text=None, latency_ms=(time.perf_counter() - t0) * 1000, model_version=self.cfg["model"])
        if self.cache.stores:
            self.cache.put("evon", key, text.encode())
        return EvonResponse(status="OK", text=text, latency_ms=(time.perf_counter() - t0) * 1000, model_version=self.cfg["model"])

    def _simulate(self, req: EvonRequest, key: str) -> EvonResponse:
        assert self._sim is not None
        rng = random.Random(int(key[:12], 16))
        latency = rng.uniform(*LATENCY_MS)  # reported, never slept: bulk runs stay fast
        if latency > req.timeout_ms:
            return EvonResponse(status="TIMEOUT", text=None, latency_ms=float(req.timeout_ms), model_version=MODEL)
        if req.purpose == "paraphrase":
            text = self._sim.paraphrase(req.prompt, rng)
        else:
            text = "I think the answer is: not json" if rng.random() < INVALID_JSON else self._sim.parse(req.prompt, rng)
        return EvonResponse(status="OK", text=text, latency_ms=latency, model_version=MODEL)
