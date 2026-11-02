"""Server-sent events for the console (C13): a hub that fans events out to every open page, and `summarize`, which turns the messages of the
backend into the small JSON documents the page draws. The page never sees a ScoredProposal; it sees what an operator needs from it."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.encoders import jsonable_encoder

QUEUE = 2000
KEEPALIVE_S = 15.0


class Hub:
    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[str]] = set()

    def publish(self, kind: str, payload: Any) -> None:
        if not self._subs:
            return
        data = json.dumps(summarize(kind, payload), default=str)
        frame = f"event: {kind}\ndata: {data}\n\n"
        for q in self._subs:
            if q.full():
                q.get_nowait()  # a slow page loses its oldest event rather than stalling the server
            q.put_nowait(frame)

    async def stream(self, first: dict[str, Any]) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(QUEUE)
        self._subs.add(q)
        try:
            yield f"event: state\ndata: {json.dumps(jsonable_encoder(first), default=str)}\n\n"
            while True:
                try:
                    yield await asyncio.wait_for(q.get(), KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subs.discard(q)


def summarize(kind: str, payload: Any) -> Any:
    if kind == "audit":
        return audit(payload)
    if kind == "view":
        return {"session_id": payload["session_id"], **view(payload["view"])}
    if kind == "sentinel":
        return {"event": jsonable_encoder(payload["event"]), "states": jsonable_encoder(payload["states"]), "lease_expiry": payload["lease_expiry"]}
    if kind == "gold":
        g = jsonable_encoder(payload)
        return {k: g[k] for k in ("call_id", "scenario", "gold_intent", "gold_slots", "tags", "split")} | {
            "snr_db": g["channel_params"].get("snr_db"), "noise": g["channel_params"].get("noise"), "codec": g["channel_params"].get("codec")}
    if kind == "control":
        c = jsonable_encoder(payload)
        return {"session_id": c["session_id"], "plan_id": c["plan_id"], "text": c["text"], "duration_ms": c["duration_ms"], "expects": c["expects"]["kind"],
                "probe": (c["expects"].get("probe_spec") or {}).get("kind")}
    if kind == "registry":
        return registry(payload)
    return jsonable_encoder(payload)


def audit(rec: Any) -> dict[str, Any]:
    p = rec.payload
    head = {"seq": rec.seq, "ts": rec.ts, "type": rec.type, "hash": rec.hash}
    if rec.type == "DECISION":
        return head | decision(p)
    return head | jsonable_encoder(p)


def decision(p: dict[str, Any]) -> dict[str, Any]:
    sp, d = p["sp"], p["decision"]
    eb = sp["eb"]
    ref = d["cert_ref"]
    return {"call_id": p["call_id"], "turn_id": p["turn_id"], "stage": p["stage_index"], "mode": p["mode"], "cell": p["cell"],
            "decision": {"kind": d["kind"], "reason": d["reason_code"], "lam": ref["lam"] if ref else None, "rung": ref["rung"] if ref else None,
                         "hedged": ref["hedged"] if ref else False, "baseline": d["baseline_shadow"]["kind"]},
            "s": sp["s"], "intent": sp.get("intent") or sp["ap"]["intent"], "text": " ".join(w["w"] for w in eb["words"]), "lang": eb["lang"],
            "words": [{"w": w["w"], "conf": w["conf"]} for w in eb["words"]],
            "entities": [{"id": e["id"], "type": e["type"], "surface": e["surface"], "min_conf": e["features"]["min_conf"],
                          "candidates": [{"value": c["value"], "p": c["p"]} for c in e["candidates"][:3]]} for e in eb["lattice"]["entities"]],
            "cqi": eb["profile"]["cqi"], "snr_db": eb["profile"]["snr_db"],
            "slots": {k: {"top": v["top"], "p": v["p_top"], "second": v["runner_up"], "p2": v["p_second"]} for k, v in sp["slots"].items()},
            "flags": {**{k: v for k, v in eb["flags"].items() if v}, "degraded": sp["flags"]["degraded"]},
            "probe": (p["probe_outcome"] or {}).get("spec", {}).get("kind"), "latency_ms": p["latency_ms"]}


def view(v: Any) -> dict[str, Any]:
    return {"account": v.account, "today": v.today, "parties": [{"id": x.id, "display": x.display, "balance": x.balance} for x in v.parties],
            "pending": [{"entry_id": e.entry_id, "intent": e.intent, "party_id": e.party_id, "amount": e.amount_inr, "date": e.date, "status": e.status}
                        for e in v.pending_entries]}


def registry(snap: Any) -> dict[str, Any]:
    return {"version": snap.version, "lease_expiry": snap.lease_expiry, "scorer_version": snap.scorer_version,
            "cells": {c: {"state": e.state, "effective": jsonable_encoder(e.effective), "ladder": jsonable_encoder(e.ladder)} for c, e in snap.cells.items()}}
