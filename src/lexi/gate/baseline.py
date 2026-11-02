"""Naive-baseline shadow (C7): what a plain word-confidence threshold would have done. Computed for every decision, never executed."""

from lexi.common.catalog import Catalog
from lexi.common.types import BaselineShadow, ScoredProposal
from lexi.gate.rules import asr_block, is_duplicate, limit_block, missing_slots


def shadow(sp: ScoredProposal, catalog: Catalog, theta_b: float) -> BaselineShadow:
    """COMMIT iff AP valid, hard limits ok and min_conf_critical >= theta_b (theta_b is matched to LEXI's autonomy on Valid by C15)."""
    valid = asr_block(sp) is None and not missing_slots(sp, catalog) and limit_block(sp, catalog) is None and not is_duplicate(sp)
    mc = sp.baseline_signals.get("min_conf_critical")
    return BaselineShadow(kind="COMMIT" if valid and mc is not None and mc >= theta_b else "ESCALATE")
