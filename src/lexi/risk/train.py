"""Scorer training (C5 step 4): LightGBM with monotone constraints, then isotonic on Valid. Offline only."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

from lexi.risk.bundle import ScorerBundle
from lexi.risk.calibrate import apply_isotonic, ece, fit_isotonic
from lexi.risk.features import MONOTONE, NAMES

PARAMS = {"n_estimators": 300, "max_depth": 6, "num_leaves": 31, "learning_rate": 0.05, "min_child_samples": 10, "subsample": 0.8,
          "subsample_freq": 1, "colsample_bytree": 0.8, "n_jobs": 1, "random_state": 0, "deterministic": True, "force_row_wise": True, "verbose": -1}
TAU = {"party": 1.0, "amount": 1.0, "date": 1.0}  # ponytail: temperatures stay 1.0; fitting them moves the features, see docs/DECISIONS.md D-006


def _matrix(rows: Sequence[dict[str, Any]], dropped: set[str]) -> np.ndarray:
    return np.array([[np.nan if n in dropped or r["features"].get(n) is None else r["features"][n] for n in NAMES] for r in rows], dtype=float)


def train(rows_train: Sequence[dict[str, Any]], rows_valid: Sequence[dict[str, Any]], out_dir: Path, *,
          drop_features: Sequence[str] = ()) -> ScorerBundle:
    dropped = set(drop_features)
    y = np.array([r["y"] for r in rows_train])
    yv = np.array([r["y"] for r in rows_valid])
    if len(set(y.tolist())) < 2 or len(set(yv.tolist())) < 2:
        raise ValueError("training and validation rows both need errors and non-errors")
    clf = lgb.LGBMClassifier(monotone_constraints=[0 if n in dropped else m for n, m in zip(NAMES, MONOTONE, strict=True)], **PARAMS)  # type: ignore[arg-type]
    clf.fit(_matrix(rows_train, dropped), y)
    raw_valid = np.asarray(clf.predict_proba(_matrix(rows_valid, dropped)))[:, 1]
    iso = fit_isotonic(raw_valid, yv.tolist())
    schema = {"features": NAMES, "dropped": sorted(dropped), "monotone": MONOTONE, "n_train": len(y), "n_valid": len(yv),
              "valid_ece": ece(apply_isotonic(iso, raw_valid), yv.tolist())}
    bundle = ScorerBundle(clf.booster_.model_to_string(), iso, dict(TAU), schema)
    bundle.save(out_dir / bundle.version)
    return bundle
