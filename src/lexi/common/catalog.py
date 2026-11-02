"""config/catalog.yaml loader and validator (4.3-4.7, 10.3). Validated at startup; its sha256 is the catalog_version."""

import itertools
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError
from scipy.stats import binom

from lexi.common.errors import CatalogError
from lexi.common.hashing import sha256_hex
from lexi.common.types import Cqi, Intent, Limits, Tier

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


class ActionCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Tier
    slots: list[str]
    critical: list[str]


class GridCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: int
    steps_per_octave: int
    max_lambda: float
    n_target_errors: int


class CqiCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q3_snr_db: float
    q3_plc: float
    q1_snr_db: float
    q1_plc: float
    short_speech_ms: float


class Catalog(BaseModel):
    version: str
    profile: str
    languages: list[str]
    alpha: dict[str, float]
    calls: int
    delta: float
    rungs: list[float]
    hedged: dict[str, float]
    grid: GridCfg
    actions: dict[str, ActionCfg]
    limits: Limits
    cqi: CqiCfg
    wb_above_hz95: float
    profiler_version: str

    # ---- tiers and cells (4.4, 4.6)
    @property
    def tiers(self) -> list[str]:
        return sorted(self.alpha)

    def tier_of(self, intent: Intent) -> Tier:
        """LEAN has a single tier: T2 actions use T1 (4.7)."""
        t: Tier = self.actions[intent].tier
        return t if t in self.alpha else "T1"

    def cqi_of(self, snr_db: float, plc_fraction: float) -> Cqi:
        c = self.cqi
        if snr_db < c.q3_snr_db or plc_fraction > c.q3_plc:
            return "Q3"
        if snr_db >= c.q1_snr_db and plc_fraction <= c.q1_plc:
            return "Q1"
        return "Q2"

    @staticmethod
    def cell_key(lang: str, cqi: str, tier: str) -> str:
        return f"{lang}.{cqi}.{tier}"

    def cells(self) -> list[str]:
        return [self.cell_key(lang, q, t) for lang, q, t in itertools.product(self.languages, ("Q1", "Q2", "Q3"), self.tiers)]

    @staticmethod
    def tier_in(cell: str) -> str:
        return cell.rsplit(".", 1)[1]

    # ---- risk budgets and statistical resolution (10.3)
    def alpha_of(self, cell: str, rung: int = 0) -> float:
        return self.alpha[self.tier_in(cell)] * self.rungs[rung]

    def n_target(self, alpha_r: float) -> int:
        return n_needed(alpha_r, self.delta, self.grid.n_target_errors)

    def critical_slots(self, intent: Intent) -> list[str]:
        return self.actions[intent].critical


@cache
def n_needed(alpha: float, delta: float, errors: int) -> int:
    """Smallest n with BinomialCDF(errors; n, alpha) <= delta (exact; reproduces the table in 10.3)."""
    n = errors + 1
    while binom.cdf(errors, n, alpha) > delta:
        n += 1
    return n


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    with open(config_dir / name, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise CatalogError(f"{name}: expected a mapping at top level")
    return data


def load_catalog(path: Path | None = None, profile: str | None = None) -> Catalog:
    path = path or CONFIG_DIR / "catalog.yaml"
    raw_bytes = path.read_bytes()
    raw = yaml.safe_load(raw_bytes)
    try:
        prof_name = profile or raw["profile"]
        prof = raw["profiles"][prof_name]
        actions = {k: ActionCfg(**v) for k, v in raw["actions"].items()}
        cat = Catalog(
            version="cat-" + sha256_hex(raw_bytes + prof_name.encode())[:12],
            profile=prof_name, languages=prof["languages"], alpha=prof["alpha"], calls=prof["calls"],
            delta=raw["delta"], rungs=raw["rungs"], hedged=raw["hedged"], grid=GridCfg(**raw["grid"]),
            actions=actions, limits=Limits(**raw["limits"]), cqi=CqiCfg(**raw["cqi"]),
            wb_above_hz95=raw["bandwidth"]["wb_above_hz95"], profiler_version=raw["profiler_version"])
    except (KeyError, TypeError, ValidationError) as e:
        raise CatalogError(f"{path.name}: {e}") from e
    _validate(cat)
    return cat


def _validate(cat: Catalog) -> None:
    if not cat.languages or any(lang not in ("hi", "ta") for lang in cat.languages):
        raise CatalogError("languages must be a non-empty subset of hi, ta")
    if sorted(cat.rungs, reverse=True) != cat.rungs or cat.rungs[0] != 1.0:
        raise CatalogError("rungs must start at 1.0 and decrease (RG0 = alpha)")
    if not 0 < cat.delta < 1 or any(not 0 < a < 1 for a in cat.alpha.values()):
        raise CatalogError("alpha and delta must be in (0, 1)")
    if cat.limits.hard_max_inr < cat.limits.max_autonomous_amount_inr:
        raise CatalogError("hard_max_inr must be >= max_autonomous_amount_inr")
    if cat.cqi.q1_snr_db <= cat.cqi.q3_snr_db:
        raise CatalogError("cqi: q1_snr_db must exceed q3_snr_db")
    for name, a in cat.actions.items():
        if not set(a.slots) <= {"party", "amount", "date"} or a.critical != ["intent", *a.slots]:
            raise CatalogError(f"action {name}: critical slots must be intent plus its slots")
