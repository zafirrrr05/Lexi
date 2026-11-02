"""Packet-loss concealment indicators (C2): exact-zero runs >= 10 ms, or a frame that repeats the previous one
(normalised correlation > 0.999)."""

import numpy as np

ZERO_RUN = 80  # 10 ms at 8 kHz
REPEAT_CORR = 0.999


def _zero_run(frame: np.ndarray) -> bool:
    nonzero = np.flatnonzero(frame != 0)
    return int((np.diff(np.concatenate(([-1], nonzero, [len(frame)]))) - 1).max()) >= ZERO_RUN


def concealed(frames: np.ndarray, prev: np.ndarray | None) -> np.ndarray:
    """Boolean per frame; `prev` is the last frame of the previous call so a stream can be fed in pieces."""
    out = np.zeros(len(frames), dtype=bool)
    f = frames.astype(np.float64)
    for i in range(len(frames)):
        a = f[i - 1] if i else (prev.astype(np.float64) if prev is not None else None)
        rep = False
        if a is not None:
            den = np.linalg.norm(a) * np.linalg.norm(f[i])
            rep = den > 0 and float(a @ f[i]) / den > REPEAT_CORR
        out[i] = _zero_run(frames[i]) or rep
    return out
