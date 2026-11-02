import os

import pytest

from lexi.audit.log import AuditLog
from lexi.common.clock import ManualClock
from lexi.common.errors import AuditError


async def test_torn_tail_after_crash_is_dropped_and_chain_continues(tmp_path):
    log = AuditLog(tmp_path, ManualClock())
    await log.start()
    for i in range(3):
        await log.append("DECISION", {"i": i})
    await log.close()
    seg = next(tmp_path.glob("seg-*.jsonl"))
    with open(seg, "ab") as f:
        f.write(b'{"seq": 4, "prev_ha')  # crash in the middle of a write, never acknowledged
    again = AuditLog(tmp_path, ManualClock())
    await again.start()
    assert again.head.seq == 3
    await again.append("DECISION", {"i": 3})
    assert again.verify() == (True, 4, None)
    await again.close()


async def test_fsync_failure_fails_append_signals_unhealthy_and_recovers(tmp_path, monkeypatch):
    signals = []
    log = AuditLog(tmp_path, ManualClock(), on_health=signals.append)
    await log.start()
    await log.append("DECISION", {"i": 0})
    real = os.fsync

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(AuditError):
        await log.append("DECISION", {"i": 1})
    assert signals[-1].component == "C12" and not signals[-1].healthy
    monkeypatch.setattr(os, "fsync", real)
    ack = await log.append("DECISION", {"i": 2})
    assert ack.seq == 2 and signals[-1].healthy  # the failed record left no trace on disk
    assert log.verify() == (True, 2, None)
    await log.close()
