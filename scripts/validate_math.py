"""Validation by simulation of spec 10.5, through the real CERT-v1 (authority/) and detector (sentinel/) code.

    python scripts/validate_math.py all|quick|a|b|c|d

(a) certificate coverage, (b) false-alarm run length, (c) M2 detection delay, (d) clearing after a false alarm. One fixed seed; every table
prints the figure published in the spec next to the measured one. `quick` runs all four at reduced size (< 60 s); `all` at the published size.
Exit status 1 if a false-alarm run length falls below its threshold: ARL >= c is what the theory promises."""

import random
import statistics
import sys
import time
from collections.abc import Callable
from types import SimpleNamespace

import numpy as np
from scipy.integrate import quad
from scipy.stats import beta

from lexi.authority.cert_v1 import certify
from lexi.authority.grid import design
from lexi.common.catalog import load_catalog, load_yaml, n_needed
from lexi.sentinel.m1 import M1
from lexi.sentinel.m2 import M2

SEED = 20261005
CAT, CFG = load_catalog(), load_yaml("detectors.yaml")
EPS, NU = CFG["m1"]["eps_grid"], CFG["m2"]["nu_grid"]
ALPHA, DELTA = 0.02, 0.025
SIZES = {  # published size, quick size
    "a_trials": (1500, 150), "b_m1_1e3": (300, 40), "b_m1_1e4": (150, 0), "b_m2_1e3": (300, 80), "b_m2_1e4": (300, 25), "b_m2_1e5": (60, 0),
    "c_runs": (300, 60), "d_m1": (300, 60), "d_m2_alpha": (400, 30), "d_m2_double": (400, 15),
}
failures: list[str] = []


def n_runs(key: str, quick: bool) -> int:
    return SIZES[key][quick]


def table(title: str, header: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(str(r[i])) for r in [header, *rows]) for i in range(len(header))]
    print(f"\n{title}")
    for r in [header, ["-" * w for w in widths], *rows]:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(r, widths, strict=True)))


# ---------------------------------------------------------------------------- (a) coverage of CERT-v1
def draw(rng: np.random.Generator, n: int, k: float) -> list[SimpleNamespace]:
    s = rng.beta(0.5, 8, n)
    y = rng.random(n) < np.minimum(1.0, k * s)
    return [SimpleNamespace(scores=[float(s[i])], elig=[1], labels=[int(y[i])]) for i in range(n)]


def true_risk(lam: float, k: float) -> float:
    return quad(lambda s: min(1.0, k * s) * beta.pdf(s, 0.5, 8), 0, lam, limit=200)[0] / beta.cdf(lam, 0.5, 8)


def run_a(quick: bool) -> None:
    published = {1.0: ("98.5%", "0.20%", "53.5%"), 1.6: ("90.1%", "0.20%", "41.8%"), 2.5: ("60.3%", "0.53%", "35.8%")}
    trials, rows = n_runs("a_trials", quick), []
    for k, pub in published.items():
        rng = np.random.default_rng(SEED + int(k * 10))
        grid = design(draw(rng, 5000, k), ALPHA, 2000, n_needed(ALPHA, DELTA, 2), CAT.grid)  # fixed before any Calib draw
        risk = {lam: true_risk(lam, k) for lam in grid}
        certified, violations, coverage = 0, 0, []
        for _ in range(trials):
            res = certify(draw(rng, 2000, k), grid, ALPHA, DELTA)  # type: ignore[arg-type]
            if res.lam is not None:
                certified, violations = certified + 1, violations + (risk[res.lam] > ALPHA)
                coverage.append(float(beta.cdf(res.lam, 0.5, 8)))
        rows.append([f"k = {k}", f"{100 * certified / trials:.1f}%", pub[0], f"{100 * violations / trials:.2f}%", pub[1],
                     f"{100 * statistics.mean(coverage):.1f}%" if coverage else "-", pub[2]])
        if violations / trials > DELTA:
            failures.append(f"(a) k={k}: violations {violations / trials:.3f} exceed delta")
    table(f"(a) CERT-v1 coverage: alpha {ALPHA}, delta {DELTA}, n_calib 2000, {trials} trials", ["scorer", "certified", "(published)", "violations", "(published)",
          "coverage", "(published)"], rows)


# ---------------------------------------------------------------------------- (b) false-alarm run length
def arl(one_run: Callable[[int], tuple[int, bool]], runs: int, threshold: float, label: str) -> tuple[str, float]:
    results = [one_run(i) for i in range(runs)]
    mean = sum(n for n, _ in results) / max(1, sum(hit for _, hit in results))  # censored runs count at their cap, so this can only understate the ARL
    if mean < threshold:
        failures.append(f"(b) {label}: run length {mean:.0f} below {threshold:.0f}")
    return f"{mean:,.0f}", mean


def m1_null(threshold: float, cap: int) -> Callable[[int], tuple[int, bool]]:
    def one(i: int) -> tuple[int, bool]:
        rng = np.random.default_rng(SEED + i)
        m1 = M1(EPS, f"b:{i}", rng.beta(0.5, 8, 500))
        for t, s in enumerate(rng.beta(0.5, 8, cap), 1):
            if m1.update(float(s)) >= threshold:
                return t, True
            m1.grow(float(s))  # a HEALTHY cell's reference grows with live traffic
        return cap, False
    return one


def m2_stream(risk: float, threshold: float, cap: int) -> Callable[[int], tuple[int, bool]]:
    def one(i: int) -> tuple[int, bool]:
        rng, m2 = random.Random(SEED + i), M2(ALPHA, NU)
        for t in range(1, cap + 1):
            if m2.update(int(rng.random() < risk)) >= threshold:
                return t, True
        return cap, False
    return one


def run_b(quick: bool) -> None:
    cases = [("M1", 1e3, "b_m1_1e3", m1_null(1e3, 60_000), "5,256 decisions"), ("M1", 1e4, "b_m1_1e4", m1_null(1e4, 300_000), "55,051 decisions"),
             ("M2", 1e3, "b_m2_1e3", m2_stream(ALPHA, 1e3, 40_000), "1,642 commits"), ("M2", 1e4, "b_m2_1e4", m2_stream(ALPHA, 1e4, 400_000), "18,101 commits"),
             ("M2", 1e5, "b_m2_1e5", m2_stream(ALPHA, 1e5, 3_000_000), "159,397 commits")]
    rows = []
    for det, thr, key, one, pub in cases:
        runs = n_runs(key, quick)
        rows.append([det, f"{thr:.0e}", str(runs), arl(one, runs, thr, f"{det} {thr:.0e}")[0] if runs else "skipped (quick)", pub])
    table("(b) false-alarm average run length under the null (ARL must exceed the threshold)", ["detector", "threshold", "runs", "mean run length", "(published)"], rows)


# ---------------------------------------------------------------------------- (c) detection delay of M2
def run_c(quick: bool) -> None:
    published = {0.04: ("170 [531]", "615 [1,612]", "1,729 [5,358]"), 0.06: ("86 [200]", "179 [515]", "288 [763]"),
                 0.10: ("42 [93]", "61 [144]", "93 [197]"), 0.25: ("15 [26]", "21 [35]", "27 [44]")}
    runs, rows = n_runs("c_runs", quick), []
    for risk, pub in published.items():
        delays: dict[float, list[int]] = {1e3: [], 1e4: [], 1e5: []}
        for i in range(runs):  # one stream serves all three thresholds
            rng, m2, found = random.Random(SEED + i), M2(ALPHA, NU), set()
            for t in range(1, 60_000):
                sr = m2.update(int(rng.random() < risk))
                for thr in delays:
                    if sr >= thr and thr not in found:
                        found.add(thr)
                        delays[thr].append(t)
                if len(found) == 3:
                    break
        cells = [f"{statistics.median(d):,.0f} [{np.percentile(d, 90):,.0f}]  vs {p}" for d, p in zip(delays.values(), pub, strict=True)]
        rows.append([f"{risk:.2f}", *cells])
    table(f"(c) M2 detection delay in labeled commits, alpha {ALPHA}: median [90th percentile], {runs} runs", ["true risk", "c = 1e3", "c = 1e4", "c = 1e5"], rows)


# ---------------------------------------------------------------------------- (d) clearing after a false alarm
def clearing_m1(i: int) -> int:
    rng = np.random.default_rng(SEED + i)
    m1, calm, alarmed, t = M1(EPS, f"d:{i}", rng.beta(0.5, 8, 500)), 0, False, 0
    for s in rng.beta(0.5, 8, 200_000):
        sr = m1.update(float(s))
        if not alarmed:
            alarmed = sr >= 1e3
            m1.grow(float(s))
            continue
        t, calm = t + 1, calm + 1 if sr < CFG["m1"]["clear_below"] else 0
        if calm >= CFG["m1"]["clear_decisions"]:
            return t
    return t


def clearing_m2(risk: float, n_calm: int, i: int, cap: int = 20_000) -> int | None:
    rng, m2, alarmed, calm = random.Random(SEED + i), M2(ALPHA, NU), False, 0
    for t in range(1, 400_000):
        sr = m2.update(int(rng.random() < risk))
        if not alarmed:
            alarmed = sr >= 1e3
            t0 = t
            continue
        calm = calm + 1 if sr < 20 else 0
        if calm >= n_calm:
            return t - t0
        if t - t0 >= cap:
            return None
    return None


def run_d(quick: bool) -> None:
    runs = n_runs("d_m1", quick)
    d = sorted(clearing_m1(i) for i in range(runs))
    rows = [["M1: SRM1 < 20 for 200 decisions", f"{statistics.median(d):,.0f} [{np.percentile(d, 90):,.0f}]", "716 [2,196]", f"{runs} runs"]]
    for n_calm, label, pub in ((100, "N = 100", "median 412 at risk = alpha; 3,070 at 2 x alpha"), (200, "N = 200", "2 x alpha: 22 of 400 clear; median 3,049 at alpha")):
        for risk, key in ((ALPHA, "d_m2_alpha"), (2 * ALPHA, "d_m2_double")):
            r = n_runs(key, quick)
            out = [clearing_m2(risk, n_calm, i) for i in range(r)]
            cleared = [x for x in out if x is not None]
            rows.append([f"M2 {label}, risk {risk:.2f}", f"median {statistics.median(cleared):,.0f}, {len(cleared)}/{r} cleared" if cleared else f"0/{r} cleared",
                         pub, f"{r} runs"])
    table("(d) clearing after a false alarm (decisions / labeled commits from the alarm)", ["rule", "measured", "(published)", "size"], rows)


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "quick"
    if arg not in ("all", "quick", "a", "b", "c", "d"):
        print(__doc__)
        return 2
    quick, start = arg == "quick", time.time()
    for name, fn in (("a", run_a), ("b", run_b), ("c", run_c), ("d", run_d)):
        if arg in ("all", "quick", name):
            fn(quick)
    print(f"\n{time.time() - start:.0f} s, seed {SEED}")
    for f in failures:
        print("FAIL", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
