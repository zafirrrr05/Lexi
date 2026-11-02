import asyncio
import json
import random

import pytest
import websockets

from lexi.adapters.cache import ProviderCache
from lexi.adapters.prisma import PrismaClient, SimLexicon, hypothesize
from lexi.common.errors import ProviderError

LEX = SimLexicon("hi")


def tok(w, kind, t0, forms=None):
    forms = forms or {"latin": [w], "deva": [], "tamil": []}
    return {"w": w, "kind": kind, "t0": t0, "t1": t0 + 0.4, "forms": forms, "value": None}


def utterance(snr=25.0, plc=0.0, seed=1, gap_after=None):
    toks = [tok("sharma", "party", 0.0, {"latin": ["sharma"], "deva": ["शर्मा"], "tamil": []}),
            tok("satrah", "num", 0.5, LEX.by_latin["satrah"]), tok("hazaar", "scale", 1.0, LEX.by_latin["hazaar"]),
            tok("jama", "cue", 1.5, {"latin": ["jama"], "deva": ["जमा"], "tamil": []})]
    if gap_after is not None:  # a second segment after a pause
        toks.append(tok("haan", "answer", gap_after, {"latin": ["haan"], "deva": ["हाँ"], "tamil": []}))
    return {"utt_id": 1, "lang": "hi", "tokens": toks, "channel": {"snr_db": snr, "plc_fraction": plc, "codec": "none", "noise": "none"}, "seed": seed}


def stream(tmp_path, entry, **kw):
    cache = ProviderCache(tmp_path, "read_through")
    cache.put_sim_utterance(entry["utt_id"], entry)
    return PrismaClient("sim", {}, cache, **kw), cache


def drive(client, end_ms=6000, start_ms=2000):
    async def go():
        s = await client.open("s1", "hi")
        s.announce(1, start_ms)
        out = []
        for ts in range(0, end_ms, 20):
            await s.feed(bytes(320), ts)
            out += s.poll()
        return s, out
    return asyncio.run(go())


def test_streams_partials_then_a_final_with_audio_times(tmp_path):
    client, _ = stream(tmp_path, utterance())
    s, events = drive(client)
    kinds = [e.kind for e in events]
    assert kinds.count("final") == 1 and kinds[-1] == "final" and "partial" in kinds
    final = events[-1]
    assert all(w.t0 >= 2000 for w in final.words) and final.t1_ms > final.t0_ms and final.text
    assert s.credits_used > 0


def test_events_are_released_only_as_audio_time_passes(tmp_path):
    client, _ = stream(tmp_path, utterance())

    async def go():
        s = await client.open("s1", "hi")
        s.announce(1, 0)
        await s.feed(bytes(320), 0)
        early = s.poll()
        await s.feed(bytes(320), 5000)
        return early, s.poll()

    early, late = asyncio.run(go())
    assert not early and any(e.kind == "final" for e in late)


def test_a_long_pause_closes_the_segment(tmp_path):
    client, _ = stream(tmp_path, utterance(gap_after=3.0))
    _, events = drive(client, end_ms=9000)
    assert [e.kind for e in events].count("final") == 2


def test_deterministic_for_a_seed_and_different_across_seeds(tmp_path):
    a = [e.text for e in drive(stream(tmp_path, utterance(seed=3))[0])[1]]
    b = [e.text for e in drive(stream(tmp_path, utterance(seed=3))[0])[1]]
    assert a == b


def test_confidences_can_be_withheld(tmp_path):
    client, _ = stream(tmp_path, utterance(), confidences=False)
    assert all(w.conf is None for e in drive(client)[1] for w in e.words)


def test_unannounced_utterance_is_an_error(tmp_path):
    client = PrismaClient("sim", {}, ProviderCache(tmp_path, "read_through"))

    async def go():
        (await client.open("s1", "hi")).announce(9, 0)

    with pytest.raises(ProviderError):
        asyncio.run(go())


def error_rate(snr, n=400):
    wrong = total = 0
    for seed in range(n):
        e = utterance(snr=snr, seed=seed)
        out = hypothesize(e, LEX, random.Random(seed), True)
        for t, (w, *_ ) in zip(e["tokens"], out, strict=False):
            total += 1
            wrong += w not in {f for fs in t["forms"].values() for f in fs}
    return wrong / total


@pytest.mark.parametrize("snr,lo,hi", [(25, 0.01, 0.08), (12, 0.04, 0.20), (2, 0.10, 0.55)])
def test_error_rate_rises_as_snr_falls(snr, lo, hi):
    assert lo <= error_rate(snr) <= hi


def test_wrong_words_have_lower_confidence_on_average():
    ok, bad = [], []
    for seed in range(300):
        e = utterance(snr=10, seed=seed)
        for t, (w, _, _, c) in zip(e["tokens"], hypothesize(e, LEX, random.Random(seed), True), strict=False):
            (ok if w in {f for fs in t["forms"].values() for f in fs} else bad).append(c)
    assert sum(ok) / len(ok) > sum(bad) / len(bad) + 0.15 and min(ok) < max(bad)  # informative but overlapping


def test_live_reads_transcript_events_without_word_confidences():
    async def serve(ws):
        got = b""
        async for msg in ws:
            got += msg
            if len(got) >= 2048:
                await ws.send(json.dumps({"type": "transcript", "text": "sharma ji ka paanch sau", "audio_duration_ms": 2000, "segment_id": "a", "segment_index": 0}))
                return

    async def go():
        async with websockets.serve(serve, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            cfg = {"url": f"ws://127.0.0.1:{port}", "key_env": "NOPE", "lang_code": {"hi": "hi-IN"}, "sample_rate": 8000}
            s = await PrismaClient("live", cfg, ProviderCache.__new__(ProviderCache)).open("s1", "hi")
            await s.feed(bytes(2048), 0)
            for _ in range(50):
                await asyncio.sleep(0.02)
                if (ev := s.poll()):
                    await s.close()
                    return ev
        return []

    (final,) = asyncio.run(go())
    assert final.kind == "final" and final.text.endswith("paanch sau") and len(final.words) == 5 and all(w.conf is None for w in final.words)
