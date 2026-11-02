"""C9 step 6: invariants checked on every write, and the limit checks C9 repeats independently of C7 (step 2b)."""

from collections.abc import Callable

from lexi.common.catalog import Catalog
from lexi.common.errors import LedgerError
from lexi.ledger.store import SIGN, Store

# entry life cycle: every write must follow one of these edges
NEXT = {None: {"PREPARED"}, "PREPARED": {"DELIVERED", "COMPENSATED", "POSTED"}, "DELIVERED": {"POSTED", "COMPENSATED"}}


def violation(cat: Catalog, store: Store, account: str, intent: str, party_id: str, amount: int | None,
              date: str | None, provenance: str) -> str | None:
    """Reason a request must be rejected, or None. Autonomous commits are held to the autonomous cap, humans to hard_max."""
    if intent not in cat.actions or store.party(account, party_id) is None:
        return "unknown_party_or_intent"
    slots = cat.actions[intent].slots
    if ("amount" in slots) != (amount is not None) or ("date" in slots) != (date is not None):
        return "slots_mismatch"
    lim = cat.limits
    if amount is not None and not 0 < amount <= (lim.hard_max_inr if provenance == "human" else lim.max_autonomous_amount_inr):
        return "over_limit"
    return None


def check_entry(cat: Catalog, store: Store, audit_has: Callable[[int, str], bool], entry_id: str) -> None:
    """Raises LedgerError if the entry's history breaks an invariant. Called inside the write transaction."""
    e = store.entry(entry_id)
    if e is None:
        raise LedgerError(f"unknown entry {entry_id}")
    why = violation(cat, store, e["account"], e["intent"], e["party_id"], e["amount_inr"], e["date"], e["provenance"])
    if why:
        raise LedgerError(f"{entry_id}: {why}")
    events = store.events(entry_id)
    prev = None
    for ev in events:
        if ev["event"] not in NEXT.get(prev, ()):
            raise LedgerError(f"{entry_id}: illegal transition {prev} -> {ev['event']}")
        prev = ev["event"]
    if prev == "POSTED":
        delivered = next((x for x in events if x["event"] == "DELIVERED"), None)
        if not audit_has(e["audit_seq"], e["audit_hash"]):
            raise LedgerError(f"{entry_id}: POSTED without an audit record")
        if e["provenance"] == "autonomous" and (delivered is None or events[-1]["ts"] < delivered["window_deadline"]):
            raise LedgerError(f"{entry_id}: POSTED before DELIVERED and window expiry")


def check_balance(store: Store, account: str) -> None:
    """balance = opening + fold of POSTED entries, recomputed here from raw rows and compared with the store's answer."""
    for p in store.parties(account):
        fold = p["opening"] + sum(SIGN[r["intent"]] * (r["amount_inr"] or 0) for r in store.posted_entries(account, p["id"]))
        if fold != store.balance(account, p["id"]):
            raise LedgerError(f"balance of {p['id']} is not the fold of its POSTED entries")
