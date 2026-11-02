from types import SimpleNamespace as NS

from lexi.console.tally import Tally

SIGN = {"CREDIT": 1, "DEBIT": -1}


def call(call_id: str, *, wrong: bool, final: str = "COMMIT", base_wrong: bool, ts: float = 10.0) -> dict:
    values = {"amount": 35000, "party": "P01", "intent": "CREDIT", "date": None}
    return {"call_id": call_id, "ts": ts, "account": "A1", "cell": "hi.Q2.T1", "final": final, "wrong": wrong, "values": values, "probes": 1,
            "names": {"P01": "Sharma"}, "baseline": {"values": {**values, "amount": 45000 if base_wrong else 35000}, "wrong": base_wrong}}


def test_an_intercepted_error_is_counted_once_and_the_ghost_ledger_moves_by_the_baseline_value() -> None:
    t = Tally({"hi.Q2.T1": 0.02}, 25000, SIGN)
    t.feed("call", call("c1", wrong=False, base_wrong=True))
    t.feed("call", call("c2", wrong=False, base_wrong=False))
    s = t.snapshot()
    assert (s["calls"], s["autonomous"], s["wrong"], s["baseline"]["wrong"], s["intercepted"]) == (2, 2, 0, 1, 1)
    assert s["intercepts"][0]["ghost"]["amount"] == 45000 and s["intercepts"][0]["lexi"]["amount"] == 35000
    assert s["net"]["A1/P01"]["ghost"] == 80000 and s["inr"]["max_loss_baseline"] == 45000


def test_celb_cap_is_alpha_times_the_amount_limit_and_only_certified_commits_count() -> None:
    t = Tally({"hi.Q2.T1": 0.02}, 25000, SIGN)
    decision = lambda hedged: NS(type="DECISION", ts=1.0, payload={  # noqa: E731
        "mode": "LIVE", "cell": "hi.Q2.T1", "decision": {"kind": "COMMIT", "cert_ref": {"hedged": hedged}}, "sp": {"slots": {"amount": {"top": 10000}}}})
    t.feed("audit", decision(False))
    t.feed("audit", decision(True))
    celb = t.snapshot()["celb"]
    assert (celb["commits"], celb["cap"], celb["est"]) == (1, 500.0, 200.0)


def test_wrong_commits_before_containment_stop_counting_at_the_revoke() -> None:
    t = Tally({}, 25000, SIGN)
    change = lambda to, ts, onset=None: NS(type="SENTINEL_EVENT", ts=ts, payload={"type": "STATE_CHANGE", "to_state": to, "shift_onset_ts": onset})  # noqa: E731
    t.feed("audit", change("WATCH", 5.0, onset=5.0))
    t.feed("call", call("c1", wrong=True, base_wrong=True, ts=6.0))
    t.feed("audit", change("REVOKED", 8.0))
    t.feed("call", call("c2", wrong=True, base_wrong=True, ts=9.0))
    s = t.snapshot()
    assert (s["wrong"], s["wrong_before_containment"], s["contained"]) == (2, 1, True)
