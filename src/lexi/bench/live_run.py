"""Latency SLOs (L1-L3) from the DECISION records of the live stack: every stage the record carries, in ms."""

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

CORE = ("c4", "c5", "decide")  # from the evidence bundle to the decision, audit fsync excluded (L1)


def latency(payloads: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """p50 and p99 in ms of every stage the DECISION records carry, and of the decision core."""
    cols = {k: [p["latency_ms"][k] for p in payloads] for k in ("c3", "c4", "c5", "decide", "audit_ack")}
    cols["core"] = [sum(p["latency_ms"][k] for k in CORE) for p in payloads]
    return {f"{k}_p{q}_ms": float(np.percentile(v, q)) for k, v in cols.items() if v for q in (50, 99)}
