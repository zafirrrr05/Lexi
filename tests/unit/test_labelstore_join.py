import pytest

from lexi.common.errors import CustodyError, LexiError
from lexi.common.splits import split_of
from lexi.common.types import AuditRecord, GoldLabel, HumanVerdict
from lexi.labelstore.ingest import LabelStore

GOLD_SLOTS = {"party": "P1", "amount": 35_000}


def script_in(split: str) -> str:
    return next(f"script-{i}" for i in range(10_000) if split_of(f"script-{i}") == split)


def gold(call_id: str, split: str = "Calib", tags=None) -> GoldLabel:
    return GoldLabel(call_id=call_id, script_id=script_in(split), split=split, scenario="S0", gold_intent="CREDIT_ENTRY",
                     gold_slots=GOLD_SLOTS, spoken=["sharma", "paintees", "hazaar"], tags=tags or [], channel_params={}, seed=1)


def sp(s: float, amount: int, *, degraded=False, repaired=False):
    return {"s": s, "flags": {"degraded": degraded}, "scorer_version": "v1", "feature_vector": {"f": s},
            "intent": None, "ap": {"intent": "CREDIT_ENTRY"},
            "slots": {"intent": {"top": "CREDIT_ENTRY"}, "party": {"top": "P1"}, "amount": {"top": amount}},
            "eb": {"lang": "hi", "profile": {"cqi": "Q2", "bandwidth_class": "NB"}, "words": [{"w": "paintalis", "conf": 0.6}],
                   "lattice": {"entities": [{"type": "AMOUNT", "features": {"repaired": repaired},
                                             "candidates": [{"value": 35_000, "parse_id": 0}, {"value": 45_000, "parse_id": 1}]}]}}}


def decision(call_id, stage, s, amount, *, mode="COLLECT", el=1, kind="PROBE", probe_outcome=None, **kw):
    return {"call_id": call_id, "turn_id": f"t{stage}", "stage_index": stage, "mode": mode, "cell": "hi.Q2.T1", "sp": sp(s, amount, **kw),
            "decision": {"kind": kind}, "decide_input": {}, "collect": {"el": el} if mode == "COLLECT" else None,
            "probe_outcome": probe_outcome, "turn_input": None, "latency_ms": {}}


class Feed:
    def __init__(self, store):
        self.store, self.seq = store, 0

    def audit(self, type_, payload, ts=1000.0):
        self.seq += 1
        self.store.ingest_audit(AuditRecord(seq=self.seq, prev_hash="p", hash="h", type=type_, payload=payload, ts=ts + self.seq))

    def close(self, call_id):
        self.audit("SESSION_EVENT", {"session_id": call_id, "account": "A", "event": "CLOSE"})


@pytest.fixture
def store(tmp_path, catalog, clock):
    s = LabelStore(tmp_path / "labels.db", catalog, clock)
    yield s
    s.close()


def collect_call(feed, store, call_id, split="Calib", close_first=False):
    g = gold(call_id, split)
    if close_first:
        feed.close(call_id)
    feed.audit("DECISION", decision(call_id, 0, 0.30, 45_000, repaired=True))
    po = {"probe_id": "pr", "kind": "CHOICE", "chosen_index": 0, "value_entity_id": None, "value": None, "answer_conf": 0.9, "latency_ms": 1,
          "spec": {"kind": "CONTRAST", "slot": "amount", "options": ["35000", "45000"]}}
    feed.audit("DECISION", decision(call_id, 1, 0.05, 35_000, probe_outcome=po))
    if not close_first:
        feed.close(call_id)
    store.ingest_gold(g)


def test_collect_trajectory_becomes_calibration_record_in_either_order(store):
    feed = Feed(store)
    collect_call(feed, store, "c1")
    collect_call(feed, store, "c2", close_first=True)
    recs = store.calibration_records("Calib")
    assert [r.call_id for r in recs] == ["c1", "c2"]
    r = recs[0]
    assert (r.cell, r.scores, r.elig, r.labels) == ("hi.Q2.T1", [0.30, 0.05], [1, 1], [1, 0])
    assert store.calibration_records("Calib", cell="ta.Q1.T1") == []
    assert len(store.calibration_records("Calib", since_ts=recs[1].ts)) == 1


def test_degraded_stage_counts_as_wrong(store):
    feed = Feed(store)
    feed.audit("DECISION", decision("c1", 0, 1.0, 35_000, el=0, degraded=True))
    feed.close("c1")
    store.ingest_gold(gold("c1"))
    assert store.calibration_records()[0].labels == [1] and store.calibration_records()[0].elig == [0]


def test_live_commit_makes_tier_g_outcome_and_notifies(store):
    seen = []
    store.on_outcome(seen.append)
    feed = Feed(store)
    feed.audit("DECISION", decision("c1", 0, 0.01, 45_000, mode="LIVE", kind="COMMIT"))
    feed.close("c1")
    store.ingest_gold(gold("c1"))
    (o,) = store.labeled_outcomes()
    assert (o.label_tier, o.y_final, o.autonomous, o.cell) == ("G", 1, True, "hi.Q2.T1") and seen == [o]


def test_human_verdict_is_tier_a_and_not_autonomous(store):
    feed = Feed(store)
    feed.audit("DECISION", decision("c1", 0, 0.9, 45_000, mode="LIVE", kind="ESCALATE"))
    store.ingest_gold(gold("c1"))
    store.ingest_verdict(HumanVerdict(ticket_id="T1", call_id="c1", values={"party": "P1", "amount": 35_000}))
    (o,) = store.labeled_outcomes()
    assert (o.label_tier, o.y_final, o.autonomous) == ("A", 0, False)


def test_training_data_never_includes_test_and_calib_is_for_c6_only(store):
    feed = Feed(store)
    collect_call(feed, store, "t1", split="Train")
    collect_call(feed, store, "v1", split="Valid")
    collect_call(feed, store, "x1", split="Test")
    assert [r["call_id"] for r in store.training_data("scorer", "Train").records] == ["t1", "t1"]
    assert {r["call_id"] for r in store.training_data("scorer", "Valid").records} == {"v1"}
    for bad in ("Test", "Calib"):
        with pytest.raises((CustodyError, ValueError)):
            store.training_data("scorer", bad)
    with pytest.raises(CustodyError):
        store.calibration_records("Test")


def test_confusion_and_probe_model_records(store):
    feed = Feed(store)
    collect_call(feed, store, "t1", split="Train")
    conf = store.training_data("confusion", "Train").records
    assert conf[0]["type"] == "token" and conf[0]["spoken"] == ["sharma", "paintees", "hazaar"] and conf[0]["heard"] == [{"w": "paintalis", "conf": 0.6}]
    assert conf[1] == {"type": "repair", "post_correct": True}
    (probe,) = store.training_data("probe_models", "Train").records
    assert probe == {"probe_kind": "CONTRAST", "slot": "amount", "lang": "hi", "cqi": "Q2", "answer_kind": "CHOICE",
                     "answer_correct": True, "options_has_gold": True}


def test_test_split_only_through_c15_once_per_tag(store):
    feed = Feed(store)
    collect_call(feed, store, "x1", split="Test")
    with pytest.raises(CustodyError):
        store.test_split("C13", "rel-1")
    ts = store.test_split("C15", "rel-1")
    assert [r["calibration"]["call_id"] for r in ts.records] == ["x1"] and ts.records[0]["gold"]["call_id"] == "x1"
    with pytest.raises(CustodyError):
        store.test_split("C15", "rel-1")
    assert store.db.execute("SELECT caller, release_tag, n FROM custody_log").fetchall() == [("C15", "rel-1", 1)]


def test_split_must_match_script_hash(store):
    bad = gold("c1", "Calib").model_copy(update={"split": "Train"})
    with pytest.raises(LexiError):
        store.ingest_gold(bad)


def test_cell_stats_report_deficit_against_n_target(store, catalog):
    feed = Feed(store)
    for i in range(3):
        collect_call(feed, store, f"c{i}")
    st = store.cell_stats({"hi.Q2.T1": [1.0, 0.5, 0.25]})
    cs = st.cells["hi.Q2.T1"]
    need = catalog.n_target(catalog.alpha_of("hi.Q2.T1", 0))
    assert cs.n_records == 3 and cs.est_accept_by_rung[0] == 3 and cs.deficit_by_rung[0] == need - 3
    assert st.cells["ta.Q1.T2"].n_records == 0 and st.cells["ta.Q1.T2"].gold_label_age_s is None


def test_call_without_a_stage_zero_cell_has_no_trajectory(store):
    feed = Feed(store)
    p = decision("c1", 0, 1.0, 35_000)
    p["cell"] = None
    feed.audit("DECISION", p)
    feed.close("c1")
    store.ingest_gold(gold("c1"))
    assert store.calibration_records() == []


def test_call_summary_puts_the_baseline_next_to_lexi(store):
    calls = []
    store.on_call(calls.append)
    feed = Feed(store)
    first = decision("c1", 0, 0.60, 45_000, mode="LIVE", kind="PROBE")  # stage 0: the naive baseline would have posted 45,000
    first["decision"] = {"kind": "PROBE", "baseline_shadow": {"kind": "COMMIT"}, "cert_ref": None}
    first["turn_input"] = {"ledger_view": {"account": "A", "parties": [{"id": "P1", "display": "Sharma ji"}]}}
    first["sp"]["eb"]["clock_ts"] = 5.0
    final = decision("c1", 1, 0.01, 35_000, mode="LIVE", kind="COMMIT")
    final["decision"] = {"kind": "COMMIT", "baseline_shadow": {"kind": "ESCALATE"}, "cert_ref": {"cert_id": "k", "rung": 0, "lam": 0.1, "hedged": False}}
    feed.audit("DECISION", first)
    feed.audit("DECISION", final)
    feed.close("c1")
    store.ingest_gold(gold("c1"))
    (c,) = calls
    assert (c["final"], c["certified"], c["wrong"], c["probes"], c["account"], c["names"]) == ("COMMIT", True, 0, 1, "A", {"P1": "Sharma ji"})
    assert c["baseline"] == {"values": {"intent": "CREDIT_ENTRY", "party": "P1", "amount": 45_000}, "wrong": 1}
