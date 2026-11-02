import random

import numpy as np
import pytest

from lexi.twin.channel import ChannelParams, amr_available, apply_channel, channel_for_cqi
from lexi.twin.noise import KINDS, make_noise
from lexi.twin.voices import train_voices, unseen_voices


@pytest.mark.parametrize("kind", KINDS)
def test_noise_has_unit_power(kind):
    x = make_noise(kind, 16000, np.random.default_rng(0))
    assert x.shape == (16000,) and abs(np.mean(x ** 2) - 1) < 1e-6


def test_babble_mixes_the_given_source():
    calls = []
    make_noise("babble", 8000, np.random.default_rng(0), lambda n: calls.append(n) or np.ones(n))
    assert len(calls) >= 3


def test_unknown_noise_kind_is_rejected():
    with pytest.raises(ValueError):
        make_noise("thunder", 10, np.random.default_rng(0))


def test_channel_is_deterministic_and_keeps_format():
    pcm = (np.sin(np.arange(16000) * 0.3) * 8000).astype(np.int16)
    p = ChannelParams("mulaw", "market", 10, 3, 0.3, False)
    a, b = apply_channel(pcm, p, np.random.default_rng(5)), apply_channel(pcm, p, np.random.default_rng(5))
    assert a.dtype == np.int16 and a.shape == pcm.shape and (a == b).all()


def test_mulaw_keeps_the_waveform():
    pcm = (np.sin(np.arange(8000) * 0.2) * 12000).astype(np.int16)
    out = apply_channel(pcm, ChannelParams("mulaw", "none", 60, 0, 0, False), np.random.default_rng(0))
    assert np.corrcoef(pcm, out)[0, 1] > 0.99


def test_loss_conceals_with_zeros_or_repeats():
    pcm = (np.random.default_rng(0).standard_normal(160 * 400) * 3000).astype(np.int16)
    out = apply_channel(pcm, ChannelParams("none", "none", 60, 8, 0, False), np.random.default_rng(1)).reshape(-1, 160)
    zero = int((~out.any(axis=1)).sum())
    repeat = int((out[1:] == out[:-1]).all(axis=1).sum())
    assert zero + repeat > 10


def test_channel_for_cqi_follows_the_ranges_of_the_spec():
    rng = random.Random(0)
    q3 = [channel_for_cqi("Q3", rng) for _ in range(50)]
    assert all(c.snr_db < 8 or c.loss_pct > 3 for c in q3)
    q1 = [channel_for_cqi("Q1", rng) for _ in range(50)]
    assert all(c.snr_db >= 18 and c.loss_pct <= 0.5 for c in q1)
    with pytest.raises(ValueError):
        channel_for_cqi("Q9", rng)


@pytest.mark.skipif(not amr_available(), reason="no AMR-NB encoder in ffmpeg")
def test_amr_round_trip():
    pcm = (np.sin(np.arange(16000) * 0.2) * 8000).astype(np.int16)
    out = apply_channel(pcm, ChannelParams("amr", "none", 60, 0, 0, False), np.random.default_rng(0))
    assert len(out) == len(pcm)


def test_voice_sets_are_large_and_disjoint():
    train, unseen = train_voices(), unseen_voices()
    assert len(train) >= 12 and not set(train) & set(unseen)
    assert all(0.85 <= v.rate <= 1.25 and abs(v.pitch_st) <= 2 for v in train + unseen)
