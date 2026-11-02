"""Twin -> gateway -> ledger over a real WebSocket (spec 14, tests/e2e): the whole stack, simulated providers, no mocks on the hot path."""

import asyncio
import random
import socket
from collections import Counter
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from lexi.adapters.timbre import TimbreClient
from lexi.cli import System, load_runtime
from lexi.common.dates import today_ist
from lexi.common.types import GoldLabel
from lexi.twin.caller_model import CallerParams
from lexi.twin.runner import TwinRunner
from lexi.twin.scenarios import SCENARIOS, plan_call
from lexi.twin.scripts import make_accounts


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def stack(tmp_path: Path) -> AsyncIterator[tuple[System, TwinRunner, list[GoldLabel], int]]:
    cfg: dict[str, Any] = load_runtime()
    cfg["paths"] = {k: str(tmp_path / v) for k, v in cfg["paths"].items()}
    cfg["clock_speed"] = 20.0
    port = free_port()
    system = System(cfg, tmp_path, mode="COLLECT", profile="LEAN", speed=20.0)
    await system.start()
    server = uvicorn.Server(uvicorn.Config(system.app(), host="127.0.0.1", port=port, log_level="error", ws_per_message_deflate=False))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    golds: list[GoldLabel] = []

    async def send_gold(g: GoldLabel) -> None:
        system.labels.ingest_gold(g)
        golds.append(g)

    runner = TwinRunner(f"ws://127.0.0.1:{port}/call", TimbreClient("sim", cfg["providers"]["timbre"], system.cache), system.cache, send_gold,
                        CallerParams(), speed=20.0, today=lambda: today_ist(system.clock.now()))
    yield system, runner, golds, port
    server.should_exit = True
    await serving
    await system.stop()


async def test_calls_run_end_to_end_and_every_turn_is_audited(stack: tuple[System, TwinRunner, list[GoldLabel], int]) -> None:
    system, runner, golds, _ = stack
    accounts = {"train": make_accounts(system.cfg["twin"]["seed"], ["hi"], 6), "unseen": make_accounts(system.cfg["twin"]["seed"], ["hi"], 6, unseen=True)}
    rng = random.Random(3)
    plans = [plan_call(rng, SCENARIOS["S0"], accounts, today_ist(system.clock.now()), split="Train") for _ in range(6)]
    used = await asyncio.gather(*(runner.run_call(p) for p in plans))
    assert all(u is not None for u in used) and len(golds) == 6
    records = list(system.audit.range(1))
    kinds = Counter(r.type for r in records)
    assert kinds["DECISION"] >= 6 and kinds["SESSION_EVENT"] == 12  # an open and a close per call
    assert system.audit.verify().ok
    finished = system.labels.counts()
    assert finished.get("Train", 0) == 6  # gold and close joined into calibration records
