"""C9 Action Ledger: two-phase, event-sourced, idempotent, compensating. The only writer of business state."""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from lexi.audit.log import Ack
from lexi.common.catalog import Catalog
from lexi.common.clock import Clock
from lexi.common.dates import today_ist
from lexi.common.errors import AuditError, LedgerError
from lexi.common.hashing import sha256_hex
from lexi.common.ids import det_id
from lexi.common.logging import get_logger, observe
from lexi.common.types import (
    CommitRequest,
    HealthSignal,
    HumanCommit,
    LedgerSignal,
    LedgerView,
    Receipt,
    UndoRequest,
)
from lexi.ledger.invariants import check_balance, check_entry, violation
from lexi.ledger.store import SIGN, STATUS, Store
from lexi.ledger.view import build_view
from lexi.ledger.window import Timers

log = get_logger("ledger")


class AuditPort(Protocol):
    async def append(self, type: str, payload: dict[str, Any], trace_id: str = "") -> Ack: ...
    def has(self, seq: int, hash: str) -> bool: ...


class Ledger:
    def __init__(self, path: Path, catalog: Catalog, clock: Clock, audit: AuditPort, *, undo_window_s: float = 5,
                 delivery_timeout_s: float = 10, unhealthy_s: float = 30,
                 on_receipt: Callable[[Receipt], Awaitable[None]], on_view: Callable[[str, LedgerView], Awaitable[None]],
                 on_health: Callable[[HealthSignal], None]) -> None:
        self.cat, self.clock, self.audit = catalog, clock, audit
        self.store = Store(path)
        self._undo_s, self._delivery_s, self._unhealthy_s = undo_window_s, delivery_timeout_s, unhealthy_s
        self._on_receipt, self._on_view, self._on_health = on_receipt, on_view, on_health
        self._lock = asyncio.Lock()  # ponytail: one writer lock, per-account locks if throughput matters
        self._timers = Timers(clock)

    # ---- lifecycle
    def seed(self, accounts: list[dict[str, Any]]) -> None:
        self.store.seed(accounts)

    async def start(self) -> None:
        """Crash recovery (3b); idempotent."""
        async with self._lock:
            for ev in self.store.unaudited_events():  # events whose audit append never happened
                await self._audit_event(ev["entry_id"], ev["event"], ev["window_deadline"], ev["reason"], recovered=True, event_id=ev["id"])
            for e in self.store.open_entries():
                if e["last"] == "PREPARED":
                    await self._transition(e["entry_id"], "COMPENSATED", reason="recovery: no receipt was heard", recovered=True)
                elif e["window_deadline"] <= self.clock.now():
                    await self._transition(e["entry_id"], "POSTED", reason="recovery: window expired", recovered=True)
                else:
                    self._timers.after(e["window_deadline"] - self.clock.now(), partial(self._expire, e["entry_id"]))

    async def close(self) -> None:
        await self._timers.close()
        self.store.close()

    # ---- H12: autonomous commit
    async def commit(self, req: CommitRequest) -> Receipt:
        t0 = self.clock.now()
        async with self._lock:
            if (e := self.store.entry_by_key(req.idempotency_key)) is not None:
                return self._receipt(e)
            sess = self.store.session(req.session_id)
            why = ("no_audit" if not self.audit.has(req.audit_seq, req.audit_hash) else
                   "no_session" if sess is None else
                   violation(self.cat, self.store, sess["account"], req.intent, req.party_id, req.amount_inr, req.date, "autonomous"))
            if why or sess is None:
                return await self._reject(req.session_id, req.idempotency_key, req.intent, req.party_id, req.amount_inr,
                                          req.date, why or "no_session", req.decision_id)
            entry_id = det_id("ent", req.idempotency_key)
            try:
                with self.store.txn():
                    self.store.insert_entry(
                        entry_id=entry_id, session_id=req.session_id, account=sess["account"], idempotency_key=req.idempotency_key,
                        intent=req.intent, party_id=req.party_id, amount_inr=req.amount_inr, date=req.date, provenance="autonomous",
                        decision_id=req.decision_id, audit_seq=req.audit_seq, audit_hash=req.audit_hash, created_ts=self.clock.now())
                    ev = self.store.add_event(entry_id, "PREPARED", self.clock.now())
                    check_entry(self.cat, self.store, self.audit.has, entry_id)
            except (sqlite3.Error, LedgerError) as exc:
                self._go_unhealthy(f"prepare failed: {exc}")
                return await self._reject(req.session_id, req.idempotency_key, req.intent, req.party_id, req.amount_inr,
                                          req.date, "ledger_error", req.decision_id)
            self._timers.after(self._delivery_s, partial(self._delivery_timeout, entry_id))
            observe("ledger_prepare_ms", (self.clock.now() - t0) * 1000)
            try:
                await self._audit_event(entry_id, "PREPARED", None, "", event_id=ev)
            except AuditError:
                return await self._on_receipt_for(self._receipt(self.store.entry(entry_id), "REJECTED"))  # compensated by the timer
            receipt = self._receipt(self.store.entry(entry_id))
            await self._on_receipt(receipt)
            await self._push_view(req.session_id)
            return receipt

    # ---- K07: operator-resolved escalation
    async def human_commit(self, hc: HumanCommit) -> Receipt:
        key = sha256_hex(f"human|{hc.ticket_id}")
        async with self._lock:
            if (e := self.store.entry_by_key(key)) is not None:
                return self._receipt(e)
            sess = self.store.session(hc.session_id)
            why = "no_session" if sess is None else violation(
                self.cat, self.store, sess["account"], hc.intent, hc.party_id, hc.amount_inr, hc.date, "human")
            if why or sess is None:
                return await self._reject(hc.session_id, key, hc.intent, hc.party_id, hc.amount_inr, hc.date, why or "no_session", None)
            entry_id = det_id("ent", key)
            # audit-before-act: the human decision is on the chain first, and that record is the entry's audit reference
            ack = await self.audit.append("LEDGER_EVENT", self._payload(
                entry_id, hc.session_id, "PREPARED", hc.intent, hc.party_id, hc.amount_inr, hc.date, "human", None, None, None,
                f"operator {hc.operator_id}", False), hc.trace_id)
            try:
                with self.store.txn():
                    self.store.insert_entry(
                        entry_id=entry_id, session_id=hc.session_id, account=sess["account"], idempotency_key=key, intent=hc.intent,
                        party_id=hc.party_id, amount_inr=hc.amount_inr, date=hc.date, provenance="human", decision_id=None,
                        audit_seq=ack.seq, audit_hash=ack.hash, created_ts=self.clock.now())
                    self.store.mark_audited(self.store.add_event(entry_id, "PREPARED", self.clock.now(), reason=f"operator {hc.operator_id}"))
            except (sqlite3.Error, LedgerError) as exc:
                self._go_unhealthy(f"human commit failed: {exc}")
                return await self._reject(hc.session_id, key, hc.intent, hc.party_id, hc.amount_inr, hc.date, "ledger_error", None)
            await self._transition(entry_id, "POSTED", reason="human provenance, no window")
            receipt = self._receipt(self.store.entry(entry_id))
            await self._on_receipt(receipt)
            await self._push_view(hc.session_id)
            return receipt

    # ---- H20: undo inside the window
    async def undo(self, req: UndoRequest) -> Receipt:
        async with self._lock:
            e = self.store.entry(req.entry_id)
            if e is None or e["session_id"] != req.session_id:
                raise LedgerError(f"unknown entry {req.entry_id} for session {req.session_id}")
            last = self.store.last_event(req.entry_id)
            if last["event"] != "DELIVERED" or self.clock.now() >= last["window_deadline"]:
                return self._receipt(e)  # too late (or never heard): the entry stands
            await self._transition(req.entry_id, "COMPENSATED", reason="undo inside the window")
            receipt = self._receipt(e)
            await self._on_receipt(receipt)
            await self._push_view(req.session_id)
            return receipt

    # ---- signals from C8 / C1
    async def signal(self, sig: LedgerSignal) -> LedgerView | None:
        async with self._lock:
            now = self.clock.now()
            if sig.type == "SESSION_OPEN":
                if sig.account_id is None or self.store.account_of(sig.account_id) is None:
                    raise LedgerError(f"unknown account {sig.account_id}")
                self.store.open_session(sig.session_id, sig.account_id, now)
                await self.audit.append("SESSION_EVENT", {"session_id": sig.session_id, "account": sig.account_id, "event": "OPEN"}, sig.trace_id)
                return self.view(sig.session_id)
            sess = self.store.session(sig.session_id)
            if sess is None:
                raise LedgerError(f"unknown session {sig.session_id}")
            if sig.type == "SESSION_CLOSE":
                self.store.close_session(sig.session_id, now)
                for e in self.store.entries_of_session(sig.session_id):
                    if e["last"] == "PREPARED":
                        await self._transition(e["entry_id"], "COMPENSATED", reason="call dropped before delivery")
                await self.audit.append("SESSION_EVENT", {"session_id": sig.session_id, "account": sess["account"], "event": "CLOSE"}, sig.trace_id)
                return None
            e = self.store.entry(sig.entry_id or "")
            if e is None or self.store.last_event(e["entry_id"])["event"] != "PREPARED":
                return None  # late or duplicate signal: nothing to do (idempotent)
            if sig.type == "RECEIPT_DELIVERED":
                await self._transition(e["entry_id"], "DELIVERED", deadline=now + self._undo_s)
                self._timers.after(self._undo_s, partial(self._expire, e["entry_id"]))
            else:
                await self._transition(e["entry_id"], "COMPENSATED", reason="receipt delivery failed")
            await self._push_view(sig.session_id)
            return None

    def view(self, session_id: str) -> LedgerView:
        today = today_ist(self.clock.now()).isoformat()
        return build_view(self.store, session_id, today, self.cat.limits, self.clock.now(), self.clock.now())

    # ---- timers
    async def _expire(self, entry_id: str) -> None:
        async with self._lock:
            last = self.store.last_event(entry_id)
            if last["event"] == "DELIVERED" and self.clock.now() >= last["window_deadline"]:
                await self._transition(entry_id, "POSTED", reason="undo window expired")
                await self._push_view(self.store.entry(entry_id)["session_id"])

    async def _delivery_timeout(self, entry_id: str) -> None:
        async with self._lock:
            if self.store.last_event(entry_id)["event"] == "PREPARED":
                await self._transition(entry_id, "COMPENSATED", reason="no delivery within the timeout")
                await self._push_view(self.store.entry(entry_id)["session_id"])

    # ---- internals
    async def _transition(self, entry_id: str, event: str, *, deadline: float | None = None, reason: str = "",
                          recovered: bool = False) -> None:
        with self.store.txn():
            ev = self.store.add_event(entry_id, event, self.clock.now(), deadline, reason)
            check_entry(self.cat, self.store, self.audit.has, entry_id)
            e = self.store.entry(entry_id)
            check_balance(self.store, e["account"])
        await self._audit_event(entry_id, event, deadline, reason, recovered=recovered, event_id=ev)

    async def _audit_event(self, entry_id: str, event: str, deadline: float | None, reason: str, *, recovered: bool = False,
                           event_id: int) -> None:
        e = self.store.entry(entry_id)
        assert e is not None
        await self.audit.append("LEDGER_EVENT", self._payload(
            entry_id, e["session_id"], event, e["intent"], e["party_id"], e["amount_inr"], e["date"], e["provenance"],
            e["decision_id"], e["audit_seq"], deadline, reason, recovered))
        self.store.mark_audited(event_id)

    @staticmethod
    def _payload(entry_id: str, session_id: str, event: str, intent: str, party_id: str, amount: int | None, date: str | None,
                 provenance: str, decision_id: str | None, audit_seq: int | None, deadline: float | None, reason: str,
                 recovered: bool) -> dict[str, Any]:
        return {"entry_id": entry_id, "session_id": session_id, "event": event, "intent": intent, "party_id": party_id,
                "amount_inr": amount, "date": date, "provenance": provenance, "decision_id": decision_id, "audit_seq": audit_seq,
                "window_deadline": deadline, "recovered": recovered, "reason": reason}

    async def _reject(self, session_id: str, key: str, intent: str, party_id: str, amount: int | None, date: str | None,
                      reason: str, decision_id: str | None) -> Receipt:
        entry_id = det_id("rej", key)
        try:
            await self.audit.append("LEDGER_EVENT", self._payload(entry_id, session_id, "REJECTED", intent, party_id, amount, date,
                                                                  "autonomous", decision_id, None, None, reason, False))
        except AuditError:
            log.error("reject not audited: %s", reason)
        sess = self.store.session(session_id)
        party = self.store.party(sess["account"], party_id) if sess else None
        receipt = Receipt(entry_id=entry_id, status="REJECTED", intent=intent, party_display=party["display"] if party else party_id,
                          amount_inr=amount, date=date, balance_after_tentative=None, session_id=session_id,
                          lang=self._lang(sess["account"]) if sess else "hi", ts=self.clock.now())
        return await self._on_receipt_for(receipt)

    async def _on_receipt_for(self, receipt: Receipt) -> Receipt:
        await self._on_receipt(receipt)
        return receipt

    def _receipt(self, e: sqlite3.Row, status: str | None = None) -> Receipt:
        st = status or STATUS[self.store.last_event(e["entry_id"])["event"]]
        party = self.store.party(e["account"], e["party_id"])
        assert party is not None
        moves_balance = SIGN[e["intent"]] != 0
        return Receipt(
            entry_id=e["entry_id"], status=st, intent=e["intent"], party_display=party["display"],
            amount_inr=e["amount_inr"], date=e["date"],
            balance_after_tentative=self.store.balance(e["account"], e["party_id"], tentative=True) if moves_balance else None,
            session_id=e["session_id"], lang=self._lang(e["account"]), ts=self.clock.now())

    def _lang(self, account: str) -> str:
        return self.store.account_of(account)["lang"]

    async def _push_view(self, session_id: str) -> None:
        await self._on_view(session_id, self.view(session_id))

    def _go_unhealthy(self, reason: str) -> None:
        self._on_health(HealthSignal(component="C9", healthy=False, since_ts=self.clock.now(), reason=reason))

        async def recover() -> None:
            self._on_health(HealthSignal(component="C9", healthy=True, since_ts=self.clock.now()))
        self._timers.after(self._unhealthy_s, recover)
