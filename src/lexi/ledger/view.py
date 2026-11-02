"""LedgerView for a session (C9 step 1): parties with balances and history, today, limits, duplicate-guard entries."""

import json

from lexi.common.types import LedgerView, Limits, PartyView, PendingEntry
from lexi.ledger.store import Store


def build_view(store: Store, session_id: str, today: str, limits: Limits, now: float, ts: float) -> LedgerView:
    account = store.session(session_id)["account"]  # type: ignore[index]
    parties = []
    for p in store.parties(account):
        posted = [r for r in store.posted_entries(account, p["id"]) if r["amount_inr"] is not None]
        hist = [*json.loads(p["amount_hist"]), *(r["amount_inr"] for r in posted)][-20:]
        last = max([p["last_entry_ts"] or 0.0, *(r["created_ts"] for r in posted)]) or None
        parties.append(PartyView(id=p["id"], display=p["display"], aliases=json.loads(p["aliases"]), ipk=p["ipk"],
                                 balance=store.balance(account, p["id"]), amount_hist=hist, last_entry_ts=last))
    pending = [PendingEntry(entry_id=r["entry_id"], intent=r["intent"], party_id=r["party_id"], amount_inr=r["amount_inr"],
                            date=r["date"], status="POSTED" if r["last"] == "POSTED" else "PENDING", ts=r["last_ts"])
               for r in store.entries_of_session(session_id)
               if r["last"] in ("PREPARED", "DELIVERED") or (r["last"] == "POSTED" and now - r["last_ts"] <= limits.duplicate_window_s)]
    return LedgerView(account=account, parties=parties, today=today, limits=limits, pending_entries=pending, ts=ts)
