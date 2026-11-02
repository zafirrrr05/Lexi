import asyncio
import base64
import json

import httpx
import numpy as np
import pytest

from lexi.adapters import timbre
from lexi.adapters.cache import ProviderCache
from lexi.adapters.timbre import TimbreClient, syllables
from lexi.common.errors import ProviderError
from lexi.common.types import TtsRequest

REQ = TtsRequest(text="paintees hazaar <p200> rupaye", lang="hi", voice="Nalini", rate=1.0)


def run(coro):
    return asyncio.run(coro)


def test_syllables_across_scripts():
    assert syllables("hazaar") == 2 and syllables("paintalis") == 3 and syllables("नमस्ते") == 3 and syllables("2500") == 4 and syllables("x") == 1


def test_sim_is_deterministic_with_word_timings():
    c = TimbreClient("sim", {}, None)
    a, b = run(c.synth(REQ)), run(c.synth(REQ))
    assert a.pcm_s16le == b.pcm_s16le and a.sample_rate == 8000 and not a.cached
    words = [w for w, _, _ in a.word_timings]
    assert words == ["paintees", "hazaar", "rupaye"]
    assert a.word_timings[2][1] - a.word_timings[1][2] >= 0.2  # the <p200> marker is silence between words
    assert abs(a.duration_ms - len(a.pcm_s16le) / 16) < 1e-6
    assert max(abs(np.frombuffer(a.pcm_s16le, "<i2"))) <= 0.51 * 32767


def test_rate_changes_duration_and_voice_changes_audio():
    c = TimbreClient("sim", {}, None)
    slow = run(c.synth(REQ.model_copy(update={"rate": 0.85})))
    fast = run(c.synth(REQ.model_copy(update={"rate": 1.25})))
    assert slow.duration_ms > fast.duration_ms * 1.2
    assert run(c.synth(REQ.model_copy(update={"voice": "Deepak"}))).pcm_s16le != run(c.synth(REQ)).pcm_s16le


def test_cache_serves_the_second_call_and_replay_never_synthesises(tmp_path):
    cache = ProviderCache(tmp_path, "read_through")
    c = TimbreClient("sim", {}, cache)
    first, second = run(c.synth(REQ)), run(c.synth(REQ))
    assert not first.cached and second.cached and first.pcm_s16le == second.pcm_s16le and second.word_timings == first.word_timings
    replay = TimbreClient("sim", {}, ProviderCache(tmp_path, "replay"))
    assert run(replay.synth(REQ)).cached
    with pytest.raises(ProviderError):
        run(replay.synth(REQ.model_copy(update={"text": "something new"})))


def test_live_parses_sse_and_strips_pause_markers(monkeypatch):
    audio = np.arange(160, dtype="<i2").tobytes()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"], seen["key"] = json.loads(request.content), request.headers["x-api-key-id"]
        sse = "event: start\ndata: {}\n\n" + "".join(f"event: chunk\ndata: {json.dumps({'audio': base64.b64encode(audio[i:i + 160]).decode(), 'is_final': i > 100})}\n\n" for i in (0, 160)) + "event: complete\ndata: {}\n\n"
        return httpx.Response(200, content=sse.encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(timbre.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("TEST_KEY", "k-123")
    c = TimbreClient("live", {"url": "https://tts.example/sse", "key_env": "TEST_KEY", "model": "timbre-v2.5"}, None)
    out = run(c.synth(REQ.model_copy(update={"rate": 2.0})))
    assert out.pcm_s16le == audio and seen["key"] == "k-123"
    assert seen["body"]["text"] == "paintees hazaar  rupaye" and seen["body"]["speed"] == 1.15 and seen["body"]["language"] == "hi-IN"


def test_live_error_event_is_a_provider_error(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(timbre.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'event: error\ndata: {"code": 500}\n\n')), **kw))
    c = TimbreClient("live", {"url": "https://tts.example/sse", "key_env": "NOPE", "model": "m"}, None)
    with pytest.raises(ProviderError):
        run(c.synth(REQ))


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        TimbreClient("fake", {}, None)
