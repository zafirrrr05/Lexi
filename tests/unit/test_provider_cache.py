import multiprocessing

import pytest

from lexi.adapters.cache import ProviderCache


def test_modes_and_roundtrip(tmp_path):
    c = ProviderCache(tmp_path, "read_through")
    assert c.get("evon", "k") is None
    c.put("evon", "k", b"v")
    assert c.get("evon", "k") == b"v" and c.serves_hits and c.stores
    assert not ProviderCache(tmp_path, "live").serves_hits and not ProviderCache(tmp_path, "replay").stores
    with pytest.raises(ValueError):
        ProviderCache(tmp_path, "bogus")


def test_sim_utterances_roundtrip_with_unicode(tmp_path):
    c = ProviderCache(tmp_path, "live")
    c.put_sim_utterance(7, {"utt_id": 7, "tokens": [{"w": "शर्मा"}]})
    assert c.get_sim_utterance(7)["tokens"][0]["w"] == "शर्मा" and c.get_sim_utterance(8) is None


def _writer(path, start):
    c = ProviderCache(path, "live")
    for i in range(start, start + 50):
        c.put("p", str(i), b"x")


def test_two_processes_can_write_the_same_file(tmp_path):
    ps = [multiprocessing.Process(target=_writer, args=(tmp_path, s)) for s in (0, 50)]
    for p in ps:
        p.start()
    for p in ps:
        p.join()
    c = ProviderCache(tmp_path, "read_through")
    assert all(c.get("p", str(i)) == b"x" for i in range(100))
