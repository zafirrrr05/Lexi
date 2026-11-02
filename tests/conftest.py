"""Shared fixtures: the validated catalog and an injected clock (P6)."""

import asyncio

import pytest

from lexi.common.catalog import Catalog, load_catalog
from lexi.common.clock import ManualClock


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return load_catalog()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


class TickClock(ManualClock):
    """Manual clock whose sleep() really waits until advance() reaches the deadline (timer-driven components)."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        super().__init__(start)
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []

    async def sleep(self, seconds: float) -> None:
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self._now + seconds, fut))
        await fut

    async def advance_async(self, seconds: float) -> None:
        await asyncio.sleep(0.01)  # timers scheduled a moment ago must register their deadline before time moves
        self._now += seconds
        for item in [w for w in self._waiters if w[0] <= self._now]:
            self._waiters.remove(item)
            item[1].set_result(None)
        for _ in range(20):  # let woken timers and their audit appends run
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)


@pytest.fixture
def tick() -> TickClock:
    return TickClock()
