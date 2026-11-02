"""Certification grid (10.3): fixed BEFORE Calib is looked at, from Valid commit rates and the planned Calib size only."""

import math
from collections.abc import Sequence

import numpy as np

from lexi.authority.cert_v1 import Trajectories
from lexi.common.catalog import GridCfg
from lexi.common.types import CalibrationRecord


def design(valid: Sequence[CalibrationRecord], alpha_r: float, n_calib: int, n_target: int, cfg: GridCfg) -> list[float]:
    """lambda_i = alpha_r x 2^(i/steps), `points` of them, from the smallest integer i (negative included: the grid may start below alpha_r) whose
    Valid-estimated commit count in a Calib set of n_calib calls reaches n_target; points above max_lambda are dropped. [] = rung absent."""
    traj = Trajectories(valid)
    eligible = traj.s[traj.el]
    if eligible.size == 0:
        return []
    i = math.floor(cfg.steps_per_octave * math.log2(max(float(np.min(eligible)), 1e-12) / alpha_r))  # below the lowest Valid score nothing commits
    while alpha_r * 2 ** (i / cfg.steps_per_octave) <= cfg.max_lambda:
        if n_calib * traj.counts(alpha_r * 2 ** (i / cfg.steps_per_octave))[0] / len(valid) >= n_target:
            grid = [alpha_r * 2 ** ((i + k) / cfg.steps_per_octave) for k in range(cfg.points)]
            return [x for x in grid if x <= cfg.max_lambda]
        i += 1
    return []
