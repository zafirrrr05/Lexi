import random
import time

import numpy as np
import pytest

from lexi.adapters.timbre import synth_sim
from lexi.common.catalog import load_catalog
from lexi.common.types import TtsRequest
from lexi.profiler.profile import Profiler
from lexi.twin.channel import ChannelParams, apply_channel, channel_for_cqi

CAT = load_catalog()
SPEECH, _ = synth_sim(TtsRequest(text="sharma ji ka paintees hazaar jama kar do agle hafte denge", lang="hi", voice="Deepak", rate=1.0))
CLEAN = np.concatenate([np.zeros(6400), SPEECH, np.zeros(4800)])  # 0.8 s lead-in gives the noise floor, 0.6 s tail


def profile(pcm: np.ndarray, turn="t1"):
    p = Profiler(CAT)
    p.feed(pcm.astype("<i2").tobytes())
    return p, p.finalize(turn)


def test_snr_mae_within_3_db_of_the_injected_snr():
    errs = []
    for noise in ("none", "babble", "market", "traffic", "rain"):
        for snr in (30, 20, 12, 6, 0):
            y = apply_channel(CLEAN, ChannelParams("none", noise, snr, 0, 0, False), np.random.default_rng(snr))
            errs.append(abs(profile(y)[1].snr_db - snr))
    assert np.mean(errs) <= 3.0


def test_vad_flags_the_synthetic_voice_as_speech():
    p = Profiler(CAT)
    flags = p.feed(np.round(SPEECH).astype("<i2").tobytes())
    assert sum(flags) / len(flags) > 0.85


def test_trailing_silence_and_turn_reset():
    y = apply_channel(CLEAN, ChannelParams("none", "none", 30, 0, 0, False), np.random.default_rng(0))
    p, prof = profile(y)
    assert 400 <= prof.trailing_silence_ms <= 700 and prof.cqi == "Q1" and prof.profiler_version == CAT.profiler_version
    assert p.trailing_silence_ms == 0 and not p.speech_started  # per-turn buffers are reset by finalize


def test_short_speech_fails_toward_the_stricter_bucket():
    blip = np.concatenate([np.zeros(6400), SPEECH[:480], np.zeros(4800)])
    prof = profile(apply_channel(blip, ChannelParams("none", "none", 30, 0, 0, False), np.random.default_rng(0)))[1]
    assert prof.short_speech and prof.cqi == "Q3"


def test_packet_loss_and_clipping_are_measured():
    lossy = apply_channel(CLEAN, ChannelParams("none", "none", 30, 8, 0, False), np.random.default_rng(1))
    assert profile(lossy)[1].plc_fraction > 0.03
    clipped = apply_channel(CLEAN, ChannelParams("none", "none", 30, 0, 0, True), np.random.default_rng(1))
    assert profile(clipped)[1].clip_fraction > 0.001


def test_finalize_is_fast_and_noise_floor_survives_turns():
    y = apply_channel(CLEAN, ChannelParams("none", "rain", 15, 0, 0, False), np.random.default_rng(2))
    p = Profiler(CAT)
    p.feed(y.tobytes())
    t0 = time.perf_counter()
    first = p.finalize("t1")
    assert time.perf_counter() - t0 < 0.005
    speech_only = np.round(apply_channel(SPEECH, ChannelParams("none", "rain", 15, 0, 0, False), np.random.default_rng(3))).astype("<i2")
    p.feed(speech_only.tobytes())  # no pause at all: only the rolling floor can supply noise frames
    assert abs(p.finalize("t2").snr_db - first.snr_db) < 6


@pytest.mark.parametrize("cqi", ["Q1", "Q2", "Q3"])
def test_channel_for_cqi_lands_in_its_measured_bucket(cqi):
    rng = random.Random(7)
    hits = sum(profile(apply_channel(CLEAN, channel_for_cqi(cqi, rng), np.random.default_rng(i)))[1].cqi == cqi for i in range(20))
    assert hits >= 15
