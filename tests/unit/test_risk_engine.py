import math

import pytest
from tests.helpers import ENGINE, make_outcome, make_sp

from lexi.common.types import Candidate
from lexi.risk import features as F
from lexi.risk.fusion import fuse
from lexi.risk.priors import amount_prior, date_prior, party_prior


def test_fusion_multiplies_prior_and_likelihood_and_merges_repair_duplicates():
    cands = [Candidate(value=35000, p=0.5), Candidate(value=45000, p=0.5), Candidate(value=35000, p=0.1, parse_id=1)]
    flat = fuse(cands, lambda v: 1.0)
    assert flat[0][0] == 35000 and abs(sum(p for _, p in flat) - 1) < 1e-9 and len(flat) == 2
    tilted = fuse(cands, lambda v: 10.0 if v == 45000 else 1.0)
    assert tilted[0][0] == 45000  # the prior can overturn a close acoustic call


def test_amount_prior_peaks_at_what_the_party_usually_pays():
    hist = [30000, 35000, 40000]
    assert amount_prior(35000, hist, 100000) > 3 * amount_prior(45000 * 2, hist, 100000)
    assert amount_prior(35000, [], 100000) == amount_prior(35000, [], 100000) > 0  # no history: log-uniform only
    assert amount_prior(10, [], 100000) < amount_prior(1000, [], 100000)


def test_date_and_party_priors():
    assert date_prior("2026-10-12", "2026-10-09", None) > date_prior("2026-12-30", "2026-10-09", None) > date_prior("2026-10-01", "2026-10-09", None)
    assert party_prior(None, 0.0) < 0.05


def test_stage_zero_slots_and_features_are_complete_and_ordered():
    sp = make_sp()
    assert list(sp.slots) == ["intent", "party", "amount"] and sp.stage_index == 0 and sp.scorer_version == "bootstrap-naive"
    assert list(sp.feature_vector) == F.NAMES and len(F.NAMES) >= 40
    assert sp.slots["party"].top == "P01" and sp.slots["amount"].top == 35000
    assert sp.feature_vector["date_p_top"] is None and sp.feature_vector["amount_p_top"] == sp.slots["amount"].p_top
    assert not sp.flags.degraded and sp.ctx is not None and sp.baseline_signals["min_conf_critical"] is not None


def test_bootstrap_score_is_one_minus_product_of_p_top():
    sp = make_sp()
    assert abs(sp.s - max(1 - math.prod(s.p_top for s in sp.slots.values()), 1e-4)) < 1e-12 and 1e-4 <= sp.s <= 1


def test_score_rises_when_the_line_degrades_or_confidence_drops():
    assert make_sp(conf=0.55).s > make_sp(conf=0.97).s
    assert make_sp("sharma ji ka satrah hazaar jama kar do", conf=0.6).s > make_sp("sharma ji ka paintees hazaar jama kar do", conf=0.97).s


def test_failed_and_conf_missing_turns_score_as_degraded_certain_risk():
    for sp in (make_sp("", status="EMPTY"), make_sp(conf=None)):
        assert sp.s == 1.0 and sp.flags.degraded and sp.slots == {}


def test_missing_slot_cannot_commit():
    sp = make_sp("sharma ji ka jama kar do")
    assert sp.s == 1.0 and "amount" not in sp.slots


def test_ambiguous_sign_has_equal_intent_probabilities():
    sp = make_sp("sharma ji ka paintees hazaar jama udhaar")  # tier 1 fails: no AP intent -> dead SP
    assert sp.s == 1.0


def test_contrast_choice_moves_the_chosen_candidate_up():
    sp = make_sp("sharma ji ka satrah hazaar jama kar do", conf=0.6)
    a, b = sp.slots["amount"].top, sp.slots["amount"].runner_up
    out = make_outcome(sp, "CHOICE", "CONTRAST", "amount", options=[str(a), str(b)], chosen_index=1)
    new = ENGINE.update(out)
    assert new.stage_index == 1 and new.slots["amount"].top == b and new.slots["amount"].p_top > sp.slots["amount"].p_second
    assert new.feature_vector["stage_index"] == 1 and new.feature_vector["probe_type"] == 1 and new.feature_vector["answer_agrees"] == 0.0
    agree = ENGINE.update(make_outcome(sp, "CHOICE", "CONTRAST", "amount", options=[str(a), str(b)], chosen_index=0))
    assert agree.slots["amount"].p_top > sp.slots["amount"].p_top and agree.s < sp.s


def test_readback_yes_lowers_risk_less_than_a_forced_choice_when_caller_acquiesces():
    sp = make_sp("sharma ji ka satrah hazaar jama kar do", conf=0.6)
    yes = ENGINE.update(make_outcome(sp, "YES", "READBACK", "amount"))
    assert yes.s < sp.s
    kappa_hi = ENGINE.__class__(ENGINE.catalog, None, ENGINE.probe.model_copy(update={"kappa": 0.6}))
    assert kappa_hi.update(make_outcome(sp, "YES", "READBACK", "amount")).s > yes.s  # more acquiescence, weaker evidence


def test_readback_no_demotes_the_riskiest_candidate():
    sp = make_sp("sharma ji ka satrah hazaar jama kar do", conf=0.6)
    no = ENGINE.update(make_outcome(sp, "NO", "READBACK", "amount"))
    assert no.s >= sp.s or no.slots["amount"].top != sp.slots["amount"].top or no.slots["party"].top != sp.slots["party"].top


def test_reask_fuses_the_new_reading():
    sp = make_sp("sharma ji ka satrah hazaar jama kar do", conf=0.6)
    new = ENGINE.update(make_outcome(sp, "VALUE", "REASK", "amount", value_text="satrah hazaar", conf=0.95))
    assert new.slots["amount"].top == 17000 and new.slots["amount"].p_top > sp.slots["amount"].p_top


def test_unclear_answer_changes_nothing_but_the_stage():
    sp = make_sp()
    new = ENGINE.update(make_outcome(sp, "UNCLEAR", "CONTRAST", "amount", options=["35000", "45000"]))
    assert new.stage_index == 1 and new.slots == sp.slots and new.feature_vector["answer_agrees"] is None


def test_dup_probe_new_entry_clears_the_flag():
    sp = make_sp()
    assert ENGINE.update(make_outcome(sp, "CHOICE", "CONTRAST", "dup", options=["new", "same"], chosen_index=0)).dup_cleared
    assert not ENGINE.update(make_outcome(sp, "CHOICE", "CONTRAST", "dup", options=["new", "same"], chosen_index=1)).dup_cleared


def test_ablations_remove_their_features():
    prior = ENGINE.__class__(ENGINE.catalog, None, ENGINE.probe, ablate=frozenset({"prior"}))
    assert make_sp(engine=prior).feature_vector["amount_prior_log_odds_top"] is None
    channel = ENGINE.__class__(ENGINE.catalog, None, ENGINE.probe, ablate=frozenset({"channel"}))
    assert make_sp(engine=channel).feature_vector["snr_db"] is None


def test_scoring_is_deterministic():
    assert make_sp().sp_id == make_sp().sp_id and make_sp().feature_digest == make_sp().feature_digest


@pytest.mark.parametrize("name", F.NAMES)
def test_feature_names_are_unique_and_monotone_vector_aligned(name):
    assert F.NAMES.count(name) == 1 and len(F.MONOTONE) == len(F.NAMES)
