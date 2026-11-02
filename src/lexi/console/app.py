"""Operator console (C13): FastAPI + SSE, one static page (13). The console holds no business state. It shows what the other components publish and
sends back the four things an operator may do: resolve an escalation (K07 HumanCommit and K06 HumanVerdict), command the Twin (K08), read an EvalReport (K32).
It imports no other component; the composition root hands it `Hooks`."""

import asyncio
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from lexi.common.types import HumanCommit, HumanVerdict, Receipt, ScenarioCommand
from lexi.console.sse import Hub, registry
from lexi.console.tally import Tally

STATIC = Path(__file__).with_name("static")
TRACE_KEPT = 600


@dataclass
class Hooks:
    human_commit: Callable[[HumanCommit], Awaitable[Receipt]]  # K07
    verdict: Callable[[HumanVerdict], None]  # K06
    command: Callable[[ScenarioCommand], Awaitable[dict[str, Any]]]  # K08
    twin: Callable[[], Awaitable[dict[str, Any]]]  # the Twin's status, for the scenario screen
    state: Callable[[], dict[str, Any]]  # everything a page that has just connected needs
    results_dir: Path  # K32: C15 writes results/<tag>/report.json, the console reads it
    tally: Mapping[str, Any]  # alpha per cell, the rupee cap of 4.8 and the sign of each intent: what the counters of screen 3 need


class Resolution(BaseModel):
    intent: str
    party_id: str
    amount_inr: int | None = None
    date: str | None = None
    operator_id: str = "operator"


def create_app(hooks: Hooks) -> tuple[FastAPI, Callable[[str, Any], None]]:
    app, hub, tally = FastAPI(title="LEXI console"), Hub(), Tally(**hooks.tally)
    latest: dict[str, Any] = {}  # the last CellStats (K16): a page that has just connected should not wait a second for it
    traces: dict[str, deque[list[Any]]] = {}  # SRM1 and SRM2 per cell over time: the sparklines of screen 2
    flush_due = False

    def snapshot() -> dict[str, Any]:
        s = hooks.state()
        reg = s["registry"]
        return jsonable_encoder({**s, "registry": registry(reg) if reg else None, "tally": tally.snapshot(), "cellstats": latest.get("cellstats"),
                                  "traces": {c: list(t) for c, t in traces.items()}})

    def publish(kind: str, payload: Any) -> None:
        nonlocal flush_due
        if kind == "cellstats":
            latest["cellstats"] = jsonable_encoder(payload)
        elif kind == "sentinel":
            for cell, st in payload["states"].items():
                traces.setdefault(cell, deque(maxlen=TRACE_KEPT)).append([round(time.time(), 2), st["srm1"], st["srm2"], st["state"]])
        if tally.feed(kind, payload) and not flush_due:
            try:  # the counters go out at most four times a second, however many calls finish
                asyncio.get_running_loop().call_later(0.25, flush)
                flush_due = True
            except RuntimeError:
                pass
        hub.publish(kind, payload)

    def flush() -> None:
        nonlocal flush_due
        flush_due = False
        hub.publish("tally", tally.snapshot())

    @app.post("/api/tally/reset")
    async def reset() -> dict[str, bool]:
        tally.reset()
        flush()
        return {"ok": True}

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return snapshot()

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(hub.stream(snapshot()), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/tickets/{ticket_id}/resolve")
    async def resolve(ticket_id: str, body: Resolution) -> dict[str, Any]:
        ticket = next((t for t in hooks.state()["tickets"] if t.ticket_id == ticket_id), None)
        if ticket is None:
            raise HTTPException(404, "unknown ticket")
        values = {"intent": body.intent, "party": body.party_id} | ({"amount": body.amount_inr} if body.amount_inr is not None else {}) | (
            {"date": body.date} if body.date else {})
        receipt = await hooks.human_commit(HumanCommit(ticket_id=ticket_id, session_id=ticket.session_id, intent=body.intent, party_id=body.party_id,  # type: ignore[arg-type]
                                                       amount_inr=body.amount_inr, date=body.date, operator_id=body.operator_id))
        hooks.verdict(HumanVerdict(ticket_id=ticket_id, call_id=ticket.session_id, values=values))
        return {"status": receipt.status, "entry_id": receipt.entry_id}

    @app.get("/api/twin")
    async def twin() -> dict[str, Any]:
        return await hooks.twin()

    @app.post("/api/twin/command")
    async def command(cmd: ScenarioCommand) -> dict[str, Any]:
        return await hooks.command(cmd)

    @app.get("/api/reports")
    async def reports() -> list[dict[str, Any]]:
        out = []
        for f in sorted(hooks.results_dir.glob("*/report.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            r = json.loads(f.read_text(encoding="utf-8"))
            out.append({"tag": f.parent.name, "passed": r.get("passed"), "generated": f.stat().st_mtime})
        return out

    @app.get("/api/reports/{tag}")
    async def report(tag: str) -> Any:
        f = hooks.results_dir / tag / "report.json"
        if not f.is_file() or tag != Path(tag).name:
            raise HTTPException(404, "unknown report")
        return json.loads(f.read_text(encoding="utf-8"))

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app, publish
