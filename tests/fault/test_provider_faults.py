import asyncio

from lexi.adapters.cache import ProviderCache
from lexi.adapters.evon import EvonClient
from lexi.adapters.prisma import PrismaClient
from lexi.common.types import EvonRequest


async def _two_frames(client):
    s = await client.open("s1", "hi")
    await s.feed(bytes(320), 0)
    await s.feed(bytes(320), 20)
    return s.poll()


def test_prisma_faults_surface_as_error_events_not_exceptions(tmp_path):
    for fault, status in (("unavailable", "UNAVAILABLE"), ("timeout", "TIMEOUT")):
        client = PrismaClient("sim", {}, ProviderCache(tmp_path, "live"), fault=fault)
        (ev,) = asyncio.run(_two_frames(client))  # one error event, not one per frame
        assert ev.kind == "error" and ev.status == status


def test_live_evon_replay_miss_is_an_error_status(tmp_path):
    c = EvonClient("live", {"url": "http://nowhere", "model": "m", "key_env": "NOPE"}, ProviderCache(tmp_path, "replay"))
    assert asyncio.run(c.complete(EvonRequest(purpose="parse", prompt="unseen"))).status == "ERROR"


def test_live_evon_unreachable_host_is_an_error_status(tmp_path):
    c = EvonClient("live", {"url": "http://127.0.0.1:1/v1", "model": "m", "key_env": "NOPE"}, ProviderCache(tmp_path, "live"))
    assert asyncio.run(c.complete(EvonRequest(purpose="parse", prompt="x", timeout_ms=300))).status in ("ERROR", "TIMEOUT")
