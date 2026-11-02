"""Timers on the injected clock: the 10 s delivery timeout and the 5 s undo window (C9 step 3)."""

import asyncio
from collections.abc import Awaitable, Callable

from lexi.common.clock import Clock
from lexi.common.logging import get_logger

log = get_logger("ledger")


class Timers:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._tasks: set[asyncio.Task[None]] = set()

    def after(self, delay_s: float, fn: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(self._run(delay_s, fn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, delay_s: float, fn: Callable[[], Awaitable[None]]) -> None:
        await self._clock.sleep(delay_s)
        try:
            await fn()
        except Exception:
            log.exception("ledger timer failed")

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
