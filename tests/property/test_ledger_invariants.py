from hypothesis import given
from hypothesis import strategies as st
from tests.unit.test_ledger_flow import ACCOUNT

from lexi.common.errors import LedgerError
from lexi.ledger.invariants import check_balance, violation
from lexi.ledger.store import Store


def _store(tmp_path_factory):
    s = Store(tmp_path_factory.mktemp("l") / "l.db")
    s.seed([ACCOUNT])
    return s


@given(amount=st.integers(-10, 200_000), human=st.booleans())
def test_amount_limits_never_overridden(tmp_path_factory, catalog, amount, human):
    s = _store(tmp_path_factory)
    why = violation(catalog, s, "A1", "CREDIT_ENTRY", "P1", amount, None, "human" if human else "autonomous")
    cap = 100_000 if human else 25_000
    assert (why is None) == (0 < amount <= cap)


@given(amounts=st.lists(st.tuples(st.sampled_from(["CREDIT_ENTRY", "DEBIT_ENTRY"]), st.integers(1, 25_000)), max_size=25))
def test_balance_is_the_fold_of_posted_entries(tmp_path_factory, amounts):
    s = _store(tmp_path_factory)
    s.open_session("S1", "A1", 0.0)
    expected = 100_000
    for i, (intent, amt) in enumerate(amounts):
        s.insert_entry(entry_id=f"e{i}", session_id="S1", account="A1", idempotency_key=f"k{i}", intent=intent, party_id="P1",
                       amount_inr=amt, date=None, provenance="human", decision_id=None, audit_seq=1, audit_hash="h", created_ts=float(i))
        s.add_event(f"e{i}", "PREPARED", float(i))
        if i % 3:                                    # a third stay PENDING and must not count
            s.add_event(f"e{i}", "POSTED", float(i))
            expected += amt if intent == "DEBIT_ENTRY" else -amt
    assert s.balance("A1", "P1") == expected
    try:
        check_balance(s, "A1")
    except LedgerError as e:  # pragma: no cover
        raise AssertionError(e) from e
