"""Injected clock (P6). The decision core never calls time.time(); it receives `now` explicitly.

`SystemClock(speed)` runs faster than wall time so that 5 s undo windows and 10 s leases do not
make bulk data collection take hours. `ManualClock` is for tests: sleep() advances time at once.
"""

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self._t0 = time.time()
        self._w0 = time.monotonic()

    def now(self) -> float:
        return self._t0 + (time.monotonic() - self._w0) * self.speed

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / self.speed)


class ManualClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    async def sleep(self, seconds: float) -> None:
        self._now += seconds
        await asyncio.sleep(0)
