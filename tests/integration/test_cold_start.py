"""Cold start over a real WebSocket (spec 12 F14, rule R0): a system with no scorer and no certificates takes live calls and commits nothing by itself."""

import asyncio
import random
import socket
from collections import Counter
from pathlib import Path
from typing import Any

import uvicorn

from lexi.adapters.timbre import TimbreClient
from lexi.cli import System, load_runtime
from lexi.common.dates import today_ist
from lexi.twin.caller_model import CallerParams
from lexi.twin.runner import TwinRunner
from lexi.twin.scenarios import SCENARIOS, plan_call
from lexi.twin.scripts import make_accounts


async def test_a_system_without_certificates_escalates_every_live_call(tmp_path: Path) -> None:
    cfg: dict[str, Any] = load_runtime()
    cfg["paths"] = {k: str(tmp_path / v) for k, v in cfg["paths"].items()}
    system = System(cfg, tmp_path, mode="LIVE", profile="LEAN", speed=20.0)
    await system.start()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(system.app(), host="127.0.0.1", port=port, log_level="error", ws_per_message_deflate=False))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        runner = TwinRunner(f"ws://127.0.0.1:{port}/call", TimbreClient("sim", cfg["providers"]["timbre"], system.cache), system.cache, _drop,
                            CallerParams(), speed=20.0, today=lambda: today_ist(system.clock.now()))
        accounts = {"train": make_accounts(cfg["twin"]["seed"], ["hi"], 6), "unseen": make_accounts(cfg["twin"]["seed"], ["hi"], 6, unseen=True)}
        rng = random.Random(5)
        plans = [plan_call(rng, SCENARIOS["S0"], accounts, today_ist(system.clock.now())) for _ in range(3)]
        await asyncio.gather(*(runner.run_call(p) for p in plans))
        kinds = Counter(r.payload["decision"]["kind"] for r in system.audit.range(1) if r.type == "DECISION")
        assert kinds and "COMMIT" not in kinds
        assert system.audit.verify().ok
    finally:
        server.should_exit = True
        await serving
        await system.stop()


async def _drop(_: Any) -> None:
    """The gold label is not needed here."""
