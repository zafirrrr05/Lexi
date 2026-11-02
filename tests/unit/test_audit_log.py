import asyncio
import json

import pytest

from lexi.audit.chain import GENESIS, recompute
from lexi.audit.log import AuditLog
from lexi.common.clock import ManualClock
from lexi.common.errors import AuditError


async def _log(tmp_path, **kw):
    log = AuditLog(tmp_path, ManualClock(), **kw)
    await log.start()
    return log


async def test_chain_links_and_verifies(tmp_path):
    log = await _log(tmp_path)
    acks = [await log.append("DECISION", {"i": i}) for i in range(5)]
    recs = list(log.range(1))
    assert [r.seq for r in recs] == [1, 2, 3, 4, 5]
    assert recs[0].prev_hash == GENESIS and recs[1].prev_hash == recs[0].hash
    assert all(r.hash == recompute(r) for r in recs)
    assert log.has(*acks[2]) and not log.has(3, "bad")
    assert log.head == acks[-1] and log.verify().ok
    await log.close()


async def test_group_commit_batches_concurrent_appends(tmp_path):
    log = await _log(tmp_path, group_commit_ms=20, group_commit_max=16)
    acks = await asyncio.gather(*[log.append("SESSION_EVENT", {"i": i}) for i in range(40)])
    assert sorted(a.seq for a in acks) == list(range(1, 41))
    assert log.verify().n_records == 40
    await log.close()


async def test_subscribers_see_records_in_order_after_ack(tmp_path):
    log = await _log(tmp_path)
    seen = []
    log.subscribe(lambda r: seen.append(r.seq))
    await asyncio.gather(*[log.append("DECISION", {"i": i}) for i in range(10)])
    assert seen == list(range(1, 11))
    await log.close()


async def test_rotation_and_restart_continue_the_chain(tmp_path):
    log = await _log(tmp_path, segment_records=4)
    for i in range(10):
        await log.append("DECISION", {"i": i})
    head = log.head
    await log.close()
    assert len(list(tmp_path.glob("seg-*.jsonl"))) == 3
    again = await _log(tmp_path, segment_records=4)
    assert again.head == head
    await again.append("DECISION", {"i": 10})
    assert again.verify() == (True, 11, None)
    await again.close()


async def test_one_flipped_byte_fails_verification(tmp_path):
    log = await _log(tmp_path)
    for i in range(6):
        await log.append("DECISION", {"amount": 25000 + i})
    await log.close()
    seg = next(tmp_path.glob("seg-*.jsonl"))
    seg.write_bytes(seg.read_bytes().replace(b"25003", b"35003"))
    bad = AuditLog(tmp_path, ManualClock())
    assert bad.verify() == (False, 3, 4)
    with pytest.raises(AuditError):
        await bad.start()


async def test_replayed_payload_is_canonical_json(tmp_path):
    log = await _log(tmp_path)
    await log.append("DECISION", {"b": 1, "a": [1, 2]})
    line = next(tmp_path.glob("seg-*.jsonl")).read_text().strip()
    assert json.loads(line)["payload"] == {"a": [1, 2], "b": 1}
    await log.close()


async def test_cancelled_caller_does_not_stop_the_writer(tmp_path):
    log = await _log(tmp_path, group_commit_ms=50)
    waiter = asyncio.create_task(log.append("DECISION", {"i": 0}))
    await asyncio.sleep(0)  # the record is queued, the batch is still open
    waiter.cancel()
    assert (await asyncio.wait_for(log.append("DECISION", {"i": 1}), 2)).seq == 2
    assert (await asyncio.wait_for(log.append("DECISION", {"i": 2}), 2)).seq == 3
    assert log.verify().n_records == 3
    await log.close()
