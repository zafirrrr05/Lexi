"""Heartbeat (C10, I6): the lease in C6 stays alive only while this loop runs. If the sentinel dies the beats stop and C7 loses autonomy within the TTL."""

from collections.abc import Awaitable, Callable

from lexi.common.types import SentinelEvent


async def run(emit: Callable[[SentinelEvent], Awaitable[None]], tick: Callable[[], Awaitable[None]],
              now: Callable[[], float], sleep: Callable[[float], Awaitable[None]], interval_s: float) -> None:
    """Beat first, then run the periodic staleness check, then wait. Runs until cancelled."""
    while True:
        await emit(SentinelEvent(type="HEARTBEAT", ts=now()))
        await tick()
        await sleep(interval_s)
