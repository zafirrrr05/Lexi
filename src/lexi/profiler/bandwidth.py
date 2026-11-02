"""Bandwidth (C2): the frequency below which 95% of speech-frame spectral energy lies; NB if <= 3.6 kHz else WB."""

import numpy as np

FRAME = 160
WINDOW = np.hanning(FRAME)
FREQS = np.fft.rfftfreq(FRAME, 1 / 8000)
QUANTILE = 0.95


def spectra(frames: np.ndarray) -> np.ndarray:
    """Power spectrum of each row (frames x 160 samples)."""
    return np.abs(np.fft.rfft(frames * WINDOW, axis=1)) ** 2


def hz95(total_spectrum: np.ndarray) -> float:
    cum = np.cumsum(total_spectrum)
    return float(FREQS[int(np.searchsorted(cum, QUANTILE * cum[-1]))]) if cum[-1] > 0 else 0.0
