"""C9 behaviour with a real audit log on disk and a clock that only moves when the test says so."""

import pytest

from lexi.audit.log import AuditLog
from lexi.common.errors import LedgerError
from lexi.common.types import CommitRequest, HumanCommit, LedgerSignal, UndoRequest
from lexi.ledger.commit import Ledger

ACCOUNT = {"account": "A1", "lang": "hi", "parties": [
    {"id": "P1", "display": "Sharma ji", "aliases": ["sharma"], "ipk": "sarma", "balance": 100_000, "amount_hist": [25_000], "last_entry_ts": None},
    {"id": "P2", "display": "Gupta traders", "aliases": ["gupta"], "ipk": "gupta", "balance": 0, "amount_hist": [], "last_entry_ts": None}]}


class Rig:
    def __init__(self, tmp_path, catalog, clock):
        self.receipts, self.views, self.health = [], [], []
        self.audit = AuditLog(tmp_path / "audit", clock)
        self.ledger = Ledger(tmp_path / "ledger.db", catalog, clock, self.audit, on_receipt=self._r, on_view=self._v, on_health=self.health.append)
        self.ledger.seed([ACCOUNT])

    async def _r(self, r):
        self.receipts.append(r)

    async def _v(self, sid, v):
        self.views.append((sid, v))

    async def start(self):
        await self.audit.start()
        await self.ledger.start()
        return await self.ledger.signal(LedgerSignal(type="SESSION_OPEN", session_id="S1", account_id="A1"))

    async def req(self, key="k1", intent="CREDIT_ENTRY", party="P1", amount=25_000, date=None, seq=None):
        ack = await self.audit.append("DECISION", {"k": key})
        return CommitRequest(decision_id="d1", session_id="S1", turn_id="t1", audit_seq=seq or ack.seq, audit_hash=ack.hash,
                             intent=intent, party_id=party, amount_inr=amount, date=date, idempotency_key=key)


@pytest.fixture
async def rig(tmp_path, catalog, tick):
    r = Rig(tmp_path, catalog, tick)
    r.view0 = await r.start()
    yield r
    await r.ledger.close()
    await r.audit.close()


async def test_session_open_returns_view(rig):
    assert rig.view0.account == "A1" and {p.id for p in rig.view0.parties} == {"P1", "P2"}
    assert rig.view0.parties[0].balance == 100_000 and rig.view0.limits.max_autonomous_amount_inr == 25_000


async def test_commit_is_pending_then_posts_after_delivery_and_window(rig, tick):
    r = await rig.ledger.commit(await rig.req())
    assert r.status == "PENDING" and r.balance_after_tentative == 75_000 and rig.receipts[-1] == r
    assert rig.ledger.view("S1").parties[0].balance == 100_000          # not POSTED yet
    await rig.ledger.signal(LedgerSignal(type="RECEIPT_DELIVERED", session_id="S1", entry_id=r.entry_id))
    await tick.advance_async(4.9)
    assert rig.ledger.store.last_event(r.entry_id)["event"] == "DELIVERED"
    await tick.advance_async(0.2)
    assert rig.ledger.store.last_event(r.entry_id)["event"] == "POSTED"
    assert rig.ledger.view("S1").parties[0].balance == 75_000
    events = [x.payload["event"] for x in rig.audit.range(1) if x.type == "LEDGER_EVENT"]
    assert events == ["PREPARED", "DELIVERED", "POSTED"]


async def test_duplicate_idempotency_key_returns_the_original_receipt(rig):
    req = await rig.req()
    a = await rig.ledger.commit(req)
    b = await rig.ledger.commit(req)
    assert a.entry_id == b.entry_id and len(rig.receipts) == 1
    assert len(rig.ledger.store.entries_of_session("S1")) == 1


async def test_commit_without_audit_record_is_rejected(rig):
    req = await rig.req(seq=999)
    r = await rig.ledger.commit(req)
    assert r.status == "REJECTED" and rig.ledger.store.entries_of_session("S1") == []


@pytest.mark.parametrize("kw", [{"amount": 25_001}, {"party": "NOPE"}, {"amount": None}, {"intent": "RESCHEDULE_DUE", "amount": 5}])
async def test_limits_and_registry_rechecked_independently(rig, kw):
    r = await rig.ledger.commit(await rig.req(**kw))
    assert r.status == "REJECTED"


async def test_undo_inside_window_compensates(rig, tick):
    r = await rig.ledger.commit(await rig.req())
    await rig.ledger.signal(LedgerSignal(type="RECEIPT_DELIVERED", session_id="S1", entry_id=r.entry_id))
    await tick.advance_async(2)
    u = await rig.ledger.undo(UndoRequest(entry_id=r.entry_id, session_id="S1"))
    assert u.status == "UNDONE"
    await tick.advance_async(10)
    assert rig.ledger.view("S1").parties[0].balance == 100_000


async def test_undo_after_window_does_not_change_the_entry(rig, tick):
    r = await rig.ledger.commit(await rig.req())
    await rig.ledger.signal(LedgerSignal(type="RECEIPT_DELIVERED", session_id="S1", entry_id=r.entry_id))
    await tick.advance_async(6)
    assert (await rig.ledger.undo(UndoRequest(entry_id=r.entry_id, session_id="S1"))).status == "POSTED"


async def test_no_delivery_within_timeout_compensates(rig, tick):
    r = await rig.ledger.commit(await rig.req())
    await tick.advance_async(10.5)
    assert rig.ledger.store.last_event(r.entry_id)["event"] == "COMPENSATED"
    assert rig.ledger.view("S1").pending_entries == []


async def test_call_drop_before_delivery_compensates(rig):
    r = await rig.ledger.commit(await rig.req())
    await rig.ledger.signal(LedgerSignal(type="SESSION_CLOSE", session_id="S1"))
    assert rig.ledger.store.last_event(r.entry_id)["event"] == "COMPENSATED"


async def test_receipt_failed_compensates(rig):
    r = await rig.ledger.commit(await rig.req())
    await rig.ledger.signal(LedgerSignal(type="RECEIPT_FAILED", session_id="S1", entry_id=r.entry_id))
    assert rig.ledger.store.last_event(r.entry_id)["event"] == "COMPENSATED"


async def test_human_commit_posts_up_to_hard_max_and_is_audited(rig):
    r = await rig.ledger.human_commit(HumanCommit(ticket_id="T1", session_id="S1", intent="DEBIT_ENTRY", party_id="P2",
                                                  amount_inr=60_000, date=None, operator_id="op1"))
    assert r.status == "POSTED" and rig.ledger.view("S1").parties[1].balance == 60_000
    big = await rig.ledger.human_commit(HumanCommit(ticket_id="T2", session_id="S1", intent="DEBIT_ENTRY", party_id="P2",
                                                    amount_inr=100_001, date=None, operator_id="op1"))
    assert big.status == "REJECTED"
    assert [x.payload["provenance"] for x in rig.audit.range(1) if x.payload.get("event") == "POSTED"] == ["human"]


async def test_duplicate_guard_entries_visible_in_view(rig, tick):
    r = await rig.ledger.commit(await rig.req())
    assert [p.status for p in rig.ledger.view("S1").pending_entries] == ["PENDING"]
    await rig.ledger.signal(LedgerSignal(type="RECEIPT_DELIVERED", session_id="S1", entry_id=r.entry_id))
    await tick.advance_async(6)
    assert [p.status for p in rig.ledger.view("S1").pending_entries] == ["POSTED"]
    await tick.advance_async(130)
    assert rig.ledger.view("S1").pending_entries == []


async def test_unknown_account_and_entry(rig):
    with pytest.raises(LedgerError):
        await rig.ledger.signal(LedgerSignal(type="SESSION_OPEN", session_id="S9", account_id="NOPE"))
    with pytest.raises(LedgerError):
        await rig.ledger.undo(UndoRequest(entry_id="x", session_id="S1"))
