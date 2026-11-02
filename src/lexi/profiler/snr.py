"""SNR (C2): snr_db = 10 log10( max(P_speech - P_noise, eps) / P_noise ); P_noise is the mean power of the quietest
20% of non-speech frames, P_speech the mean power of speech frames."""

import numpy as np

EPS = 1e-12
NOISE_QUANTILE = 0.2


def snr_db(speech_power: np.ndarray, noise_power: np.ndarray) -> float:
    pn = max(float(np.sort(noise_power)[: max(1, int(len(noise_power) * NOISE_QUANTILE))].mean()), EPS)
    ps = float(speech_power.mean())
    return float(10 * np.log10(max(ps - pn, EPS) / pn))


def nonstationarity_db(noise_power: np.ndarray) -> float:
    """Standard deviation (dB) of non-speech frame energy; high for babble and traffic."""
    return float(np.std(10 * np.log10(noise_power + EPS))) if len(noise_power) > 1 else 0.0
