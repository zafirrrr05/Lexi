"""Issuing certificates for one cell (C6 steps 2-5): precheck, stationarity, grid, CERT-v1 per rung, and a report that explains every absence."""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field

from lexi.authority import stationarity
from lexi.authority.cert_v1 import certify
from lexi.authority.grid import design
from lexi.common.catalog import Catalog
from lexi.common.hashing import digest
from lexi.common.ids import det_id
from lexi.common.types import CalibrationRecord, Certificate, Part


class RungReport(Part):
    rung: int
    alpha: float
    n_target: int
    grid: list[float]
    points: list[tuple[float, int, int, float]] = []  # (lambda, n, errors, p): the walk up the grid
    lam: float | None = None
    note: str = ""  # why the rung is absent


class CellReport(Part):
    status: Literal["ISSUED", "NO_RUNGS", "INSUFFICIENT", "NONSTATIONARY"]
    n_records: int
    peak_srm1: float | None = None
    rungs: list[RungReport] = []


class IssueReport(Part):
    scorer_version: str
    version: int | None = None  # the published registry version; None = nothing changed
    cells: dict[str, CellReport] = {}
    certificates: list[Certificate] = []
    reference: dict[str, list[float]] = Field(default_factory=dict, exclude=True)  # Calib stage-0 scores per issued cell, K14 input for the sentinel



def certify_cell(cat: Catalog, cfg: Mapping[str, Any], cell: str, calib: Sequence[CalibrationRecord], valid: Sequence[CalibrationRecord],
                 planned: Mapping[int, list[float]], *, scorer_version: str, caller_model_version: str, now: float) -> tuple[list[Certificate], CellReport]:
    calib = sorted(calib, key=lambda r: (r.ts, r.call_id))
    report = CellReport(status="INSUFFICIENT", n_records=len(calib))
    if len(calib) < cat.n_target(cat.alpha_of(cell, 0)):
        return [], report  # even RG0 cannot reach n_target: resolution is a property of the data (10.3)
    stationary, report.peak_srm1 = stationarity.check(calib, cell, cfg)
    if not stationary:
        report.status = "NONSTATIONARY"
        return [], report
    window = {"from": calib[0].ts, "to": calib[-1].ts, "hash": digest([r.call_id for r in calib])}
    certs: list[Certificate] = []
    for rung in range(len(cat.rungs)):
        alpha, n_target = cat.alpha_of(cell, rung), cat.n_target(cat.alpha_of(cell, rung))
        grid = planned[rung] if rung in planned else design(valid, alpha, len(calib), n_target, cat.grid)
        rr = RungReport(rung=rung, alpha=alpha, n_target=n_target, grid=grid)
        report.rungs.append(rr)
        if len(calib) < n_target:
            rr.note = "fewer Calib records than n_target"
        elif not grid:
            rr.note = "no grid point reaches n_target on Valid"
        else:
            res = certify(calib, grid, alpha, cat.delta)
            rr.points, rr.lam = [(p.lam, p.n, p.errors, p.p) for p in res.points], res.lam
            rr.note = "" if res.lam is not None else "the first grid point already fails the test"
            if res.lam is not None and res.p is not None:
                certs.append(Certificate(cert_id=det_id("CERT", cell, rung, scorer_version, window["hash"], now), cell=cell, rung=rung, alpha=alpha,
                                         delta=cat.delta, lam=res.lam, n=res.n, errors=res.errors, p_value=res.p, scorer_version=scorer_version,
                                         catalog_version=cat.version, calib_window=window, caller_model_version=caller_model_version,
                                         issued_at=now, state="ACTIVE"))
    report.status = "ISSUED" if certs else "NO_RUNGS"
    return certs, report
