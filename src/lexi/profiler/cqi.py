"""Bucket (C2, 4.6): Q1/Q2/Q3 from the catalog rule; fail toward the stricter bucket when there is too little speech."""

from lexi.common.catalog import Catalog
from lexi.common.types import Cqi


def bucket(catalog: Catalog, snr_db: float, plc_fraction: float, speech_ms: float) -> tuple[Cqi, bool]:
    """(cqi, short_speech)."""
    if speech_ms < catalog.cqi.short_speech_ms:
        return "Q3", True
    return catalog.cqi_of(snr_db, plc_fraction), False
