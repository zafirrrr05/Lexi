"""ScorerBundle (C5): LightGBM model, isotonic map, temperature per slot, feature schema. A directory of four files."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

from lexi.common.errors import RegistryError
from lexi.common.hashing import digest
from lexi.risk.calibrate import apply_isotonic
from lexi.risk.features import NAMES

FILES = ("model.txt", "isotonic.json", "tau.json", "schema.json")


class ScorerBundle:
    def __init__(self, model_text: str, iso: dict[str, Any], tau: dict[str, float], schema: dict[str, Any]) -> None:
        if schema["features"] != NAMES:
            raise RegistryError("scorer feature schema does not match this build")
        self.model_text, self.iso, self.tau, self.schema = model_text, iso, tau, schema
        self.booster = lgb.Booster(model_str=model_text)
        self.dropped: set[str] = set(schema.get("dropped", []))
        self.version = "sc-" + digest([model_text, iso, tau, schema])[:10]

    @classmethod
    def load(cls, directory: Path) -> "ScorerBundle":
        try:
            model, iso, tau, schema = [(directory / f).read_text(encoding="utf-8") for f in FILES]
        except (OSError, ValueError) as e:
            raise RegistryError(f"scorer bundle {directory}: {e}") from e
        return cls(model, json.loads(iso), json.loads(tau), json.loads(schema))

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in zip(FILES, (self.model_text, json.dumps(self.iso), json.dumps(self.tau), json.dumps(self.schema)), strict=True):
            (directory / name).write_text(body, encoding="utf-8")

    def raw(self, rows: Sequence[Mapping[str, float | None]]) -> np.ndarray:
        x = np.array([[np.nan if n in self.dropped or r.get(n) is None else r[n] for n in NAMES] for r in rows], dtype=float)
        return np.asarray(self.booster.predict(x, num_threads=1))

    def score_row(self, row: Mapping[str, float | None]) -> tuple[float, float]:
        """(s_raw, s) for one row."""
        raw = self.raw([row])
        return float(raw[0]), float(apply_isotonic(self.iso, raw)[0])

    def score_features(self, rows: Sequence[Mapping[str, float | None]]) -> list[float]:
        """Calibrated s in [1e-4, 1] for many rows at once (C6 re-scoring uses this)."""
        return [float(v) for v in apply_isotonic(self.iso, self.raw(rows))]
