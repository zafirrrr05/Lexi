"""Feature vector (C5 step 3): fixed order, stable names, None allowed. The LightGBM schema is this list."""

import math

from lexi.common.types import EntityFeatures, EvidenceBundle

SLOTS = ("party", "amount", "date")
SLOT_FEATURES = ("p_top", "p_second", "margin", "entropy", "min_conf", "mean_conf", "n_lexemes", "confusable_mass", "repaired",
                 "truncated", "prior_log_odds_top")
ACTION = ("naive_risk", "intent_agreement", "parse_source", "evon_latency_bucket", "intent_p_top")
CHANNEL = ("snr_db", "bandwidth_class", "plc_fraction", "clip_fraction", "noise_nonstat", "rate_proxy", "cqi")
LANGUAGE = ("lang", "code_mix_ratio", "amount_magnitude")
PROBE = ("stage_index", "probe_type", "answer_conf", "answer_agrees")
NAMES: list[str] = [f"{s}_{f}" for s in SLOTS for f in SLOT_FEATURES] + [*ACTION, *CHANNEL, *LANGUAGE, *PROBE]

# risk must not rise with these ...
FALLING = tuple(f"{s}_{f}" for s in SLOTS for f in ("p_top", "margin", "min_conf"))
# ... and must not fall with these (C5 step 4)
RISING = (*(f"{s}_{f}" for s in SLOTS for f in ("entropy", "confusable_mass")), "plc_fraction")
MONOTONE = [-1 if n in FALLING else 1 if n in RISING else 0 for n in NAMES]

PARSE_SOURCE = {"DET": 0, "EVON": 1, "DET+EVON": 2}
PROBE_TYPE = {None: 0, "CONTRAST": 1, "REASK": 2, "READBACK": 3, "INTENT_CONTRAST": 4}
CORE_SLOT = ("p_top", "p_second", "margin", "entropy", "min_conf", "mean_conf")  # missing => degraded (C5 FAILS)


def entropy3(p_top: float, p_second: float) -> float:
    rest = max(1.0 - p_top - p_second, 0.0)
    return -sum(p * math.log(p) for p in (p_top, p_second, rest) if p > 1e-12)


def slot_features(p_top: float, p_second: float, ef: EntityFeatures, prior_top: float) -> dict[str, float | None]:
    odds = min(max(prior_top, 1e-6), 1 - 1e-6)
    return {"p_top": p_top, "p_second": p_second, "margin": p_top - p_second, "entropy": entropy3(p_top, p_second),
            "min_conf": ef.min_conf, "mean_conf": ef.mean_conf, "n_lexemes": float(ef.n_lexemes), "confusable_mass": ef.confusable_mass,
            "repaired": float(ef.repaired), "truncated": float(ef.truncated), "prior_log_odds_top": math.log(odds / (1 - odds))}


def code_mix_ratio(eb: EvidenceBundle) -> float:
    """Share of Latin-script words among the words of a mixed-script utterance (0 when the utterance is one script)."""
    if not eb.flags.mixed_script:
        return 0.0
    latin = sum(all(ord(c) < 0x0900 for c in w.w) for w in eb.words)
    return latin / max(len(eb.words), 1)


def magnitude_bucket(amount: int | None) -> float | None:
    return None if amount is None else float(min(max(int(math.log10(max(amount, 1))) - 1, 0), 4))  # <100, <1k, <10k, <100k, more


def latency_bucket(ms: float | None) -> float:
    return 0.0 if ms is None else 1.0 if ms < 300 else 2.0 if ms < 500 else 3.0


def vector(parts: dict[str, float | None]) -> dict[str, float | None]:
    """The parts in schema order; unknown names are a programming error."""
    extra = set(parts) - set(NAMES)
    if extra:
        raise KeyError(f"unknown features {sorted(extra)}")
    return {n: parts.get(n) for n in NAMES}


def is_degraded(sp_slots: tuple[str, ...], fv: dict[str, float | None]) -> bool:
    return any(fv[f"{s}_{f}"] is None for s in sp_slots for f in CORE_SLOT)

