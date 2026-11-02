"""C6 properties: vectorised n(lambda), e(lambda) equal the definition of 10.2; no cell with fewer than n_target records ever gets a rung."""

import asyncio
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import CATALOG

from lexi.authority.cert_v1 import Trajectories
from lexi.authority.registry import Authority
from lexi.common.catalog import load_yaml
from lexi.common.clock import ManualClock
from lexi.common.types import CalibrationRecord

CFG = load_yaml("detectors.yaml")
CELL = "hi.Q2.T2"  # STANDARD: RG0 alpha 0.10, n_target 70


def record(i: int, stages: list[tuple[float, int, int]]) -> CalibrationRecord:
    return CalibrationRecord(call_id=f"c{i}", cell=CELL, scores=[s for s, _, _ in stages], elig=[e for _, e, _ in stages], labels=[y for _, _, y in stages],
                             feature_vectors=[{} for _ in stages], scorer_version="v", split="Calib", scenario="S0", tags=[], ts=float(i))


stages = st.lists(st.tuples(st.floats(1e-4, 1.0), st.integers(0, 1), st.integers(0, 1)), min_size=1, max_size=3)


@given(st.lists(stages, min_size=0, max_size=60), st.floats(1e-4, 1.0))
@settings(max_examples=200, deadline=None)
def test_vectorised_counts_equal_the_definition(calls, lam):
    records = [record(i, c) for i, c in enumerate(calls)]
    n = e = 0
    for c in calls:
        hit = next((y for s, el, y in c if el == 1 and s <= lam), None)
        if hit is not None:
            n, e = n + 1, e + hit
    assert Trajectories(records).counts(lam) == (n, e)


@given(st.integers(0, CATALOG.n_target(0.10) - 1), st.floats(1e-4, 0.05))
@settings(max_examples=40, deadline=None)
def test_fewer_records_than_n_target_never_get_a_rung(n, score):
    records = [record(i, [(score, 1, 0)]) for i in range(n)]  # every call commits and is right: the best case for a certificate
    with tempfile.TemporaryDirectory() as d:
        auth = Authority(CATALOG, CFG, ManualClock(), Path(d) / "certs.db", audit_append=_audit, publish=lambda s: None)
        report = asyncio.run(auth.issue("v", {CELL: records}, {CELL: records}))
        auth.store.close()
    assert not report.certificates and (n == 0 or report.cells[CELL].status == "INSUFFICIENT")


async def _audit(kind: str, payload: dict[str, object], trace_id: str) -> None:
    return None
