"""Crash recovery (C9 3b): kill the process at each step, restart, and nothing is left half-done."""

import sqlite3

import pytest
from tests.unit.test_ledger_flow import ACCOUNT

from lexi.audit.log import AuditLog
from lexi.common.types import CommitRequest, LedgerSignal
from lexi.ledger.commit import Ledger


async def _boot(tmp_path, catalog, clock):
    audit = AuditLog(tmp_path / "audit", clock)
    await audit.start()

    async def noop(*_):
        return None
    ledger = Ledger(tmp_path / "l.db", catalog, clock, audit, on_receipt=noop, on_view=noop, on_health=lambda s: None)
    ledger.seed([ACCOUNT])
    await ledger.start()
    return audit, ledger


async def _commit(audit, ledger, key="k"):
    await ledger.signal(LedgerSignal(type="SESSION_OPEN", session_id="S1", account_id="A1"))
    ack = await audit.append("DECISION", {"k": key})
    return await ledger.commit(CommitRequest(decision_id="d", session_id="S1", turn_id="t", audit_seq=ack.seq, audit_hash=ack.hash,
                                             intent="DEBIT_ENTRY", party_id="P1", amount_inr=5_000, date=None, idempotency_key=key))


async def _crash(audit, ledger):
    await ledger._timers.close()  # a killed process runs no timers; the files stay as they are
    ledger.store.db.close()
    await audit.close()


async def test_prepared_without_delivery_is_compensated_on_restart(tmp_path, catalog, tick):
    audit, ledger = await _boot(tmp_path, catalog, tick)
    r = await _commit(audit, ledger)
    await _crash(audit, ledger)
    audit, ledger = await _boot(tmp_path, catalog, tick)
    assert ledger.store.last_event(r.entry_id)["event"] == "COMPENSATED"
    assert ledger.store.balance("A1", "P1") == 100_000
    await ledger.close()
    await audit.close()


async def test_delivered_entry_posts_after_restart_once_its_deadline_passes(tmp_path, catalog, tick):
    audit, ledger = await _boot(tmp_path, catalog, tick)
    r = await _commit(audit, ledger)
    await ledger.signal(LedgerSignal(type="RECEIPT_DELIVERED", session_id="S1", entry_id=r.entry_id))
    await _crash(audit, ledger)
    tick.advance(2)
    audit, ledger = await _boot(tmp_path, catalog, tick)           # deadline not yet reached: wait out the remainder
    assert ledger.store.last_event(r.entry_id)["event"] == "DELIVERED"
    await tick.advance_async(3.5)
    assert ledger.store.last_event(r.entry_id)["event"] == "POSTED"
    await ledger.close()
    await audit.close()


async def test_delivered_entry_past_deadline_posts_immediately_on_restart(tmp_path, catalog, tick):
    audit, ledger = await _boot(tmp_path, catalog, tick)
    r = await _commit(audit, ledger)
    await ledger.signal(LedgerSignal(type="RECEIPT_DELIVERED", session_id="S1", entry_id=r.entry_id))
    await _crash(audit, ledger)
    tick.advance(60)
    audit, ledger = await _boot(tmp_path, catalog, tick)
    assert ledger.store.last_event(r.entry_id)["event"] == "POSTED"
    await ledger.close()
    await audit.close()


async def test_event_missing_from_audit_is_appended_with_recovered_flag_and_recovery_is_idempotent(tmp_path, catalog, tick):
    audit, ledger = await _boot(tmp_path, catalog, tick)
    r = await _commit(audit, ledger)
    ledger.store.add_event(r.entry_id, "DELIVERED", tick.now(), tick.now() + 5)   # written, then the process died before the audit append
    await _crash(audit, ledger)
    tick.advance(60)
    audit, ledger = await _boot(tmp_path, catalog, tick)
    recovered = [x.payload["event"] for x in audit.range(1) if x.type == "LEDGER_EVENT" and x.payload["recovered"]]
    assert recovered == ["DELIVERED", "POSTED"]
    n = audit.head.seq
    await ledger.start()
    assert audit.head.seq == n
    await ledger.close()
    await audit.close()


async def test_entries_and_events_cannot_be_rewritten(tmp_path, catalog, tick):
    audit, ledger = await _boot(tmp_path, catalog, tick)
    r = await _commit(audit, ledger)
    for sql in ("UPDATE entries SET amount_inr=1", "DELETE FROM entries", "UPDATE entry_events SET event='POSTED'", "DELETE FROM entry_events"):
        with pytest.raises(sqlite3.DatabaseError):
            ledger.store.db.execute(sql)
    assert ledger.store.entry(r.entry_id)["amount_inr"] == 5_000
    await ledger.close()
    await audit.close()


async def test_prepare_failure_rejects_and_flags_ledger_unhealthy_for_30s(tmp_path, catalog, tick):
    signals = []
    audit = AuditLog(tmp_path / "audit", tick)
    await audit.start()

    async def noop(*_):
        return None
    ledger = Ledger(tmp_path / "l.db", catalog, tick, audit, on_receipt=noop, on_view=noop, on_health=signals.append)
    ledger.seed([ACCOUNT])
    await ledger.start()
    await ledger.signal(LedgerSignal(type="SESSION_OPEN", session_id="S1", account_id="A1"))
    ledger.store.db.execute("CREATE TRIGGER boom BEFORE INSERT ON entry_events BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END")
    r = await _commit_after_open(audit, ledger)
    assert r.status == "REJECTED" and not signals[-1].healthy and signals[-1].component == "C9"
    await tick.advance_async(30.5)
    assert signals[-1].healthy
    await ledger.close()
    await audit.close()


async def _commit_after_open(audit, ledger):
    ack = await audit.append("DECISION", {"k": "x"})
    return await ledger.commit(CommitRequest(decision_id="d", session_id="S1", turn_id="t", audit_seq=ack.seq, audit_hash=ack.hash,
                                             intent="DEBIT_ENTRY", party_id="P1", amount_inr=5_000, date=None, idempotency_key="x"))
