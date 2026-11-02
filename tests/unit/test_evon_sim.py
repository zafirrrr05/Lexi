import asyncio
import json

import httpx
import pytest
from tests.helpers import EVIDENCE, turn

from lexi.adapters import evon as evon_mod
from lexi.adapters.cache import ProviderCache
from lexi.adapters.evon import EvonClient
from lexi.common.catalog import load_catalog
from lexi.common.types import EvonRequest
from lexi.parser.evon_parse import build_prompt, load_template

CAT = load_catalog()
TEMPLATE, _ = load_template()


def ask(client, text, timeout=800):
    eb = EVIDENCE.build(turn(text, conf=0.99))
    resp = asyncio.run(client.complete(EvonRequest(purpose="parse", prompt=build_prompt(TEMPLATE, eb, CAT), timeout_ms=timeout)))
    return eb, resp


def sim(tmp_path):
    return EvonClient("sim", {}, ProviderCache(tmp_path, "read_through"))


def test_sim_selects_intent_and_ids_from_the_parser_prompt(tmp_path):
    c = sim(tmp_path)
    kinds = [("{p} {n} jama udhaar", "DEBIT_ENTRY"), ("{p} {n} udhaar jama", "CREDIT_ENTRY"), ("{p} {n} agle hafte", "PROMISE_TO_PAY"), ("{p} {n} badal do kal", "RESCHEDULE_DUE")]
    ok = invalid = 0
    for i in range(600):
        fmt, intent = kinds[i % 4]
        _, resp = ask(c, fmt.format(p=["sharma ji", "verma", "gupta ji"][i % 3], n=1000 + i))
        try:
            ok += json.loads(resp.text)["intent"] == intent
        except ValueError:
            invalid += 1
    assert 0.01 <= invalid / 600 <= 0.06  # about 3% invalid JSON
    assert 0.94 <= ok / (600 - invalid) <= 1.0  # about 2% valid but wrong


def test_sim_answer_references_entities_of_the_right_type(tmp_path):
    eb, resp = ask(sim(tmp_path), "gupta ji paanch hazaar agle hafte denge")
    data = json.loads(resp.text)
    types = {e.id: e.type for e in eb.lattice.entities}
    assert {types[v] for v in data["slots"].values()} <= {"PARTY", "AMOUNT", "DATE"} and set(data["slots"]) == {"party", "amount", "date"}


def test_sim_latency_is_reported_not_slept_and_timeouts_follow_the_budget(tmp_path):
    c = sim(tmp_path)
    _, resp = ask(c, "sharma ji paintees hazaar")
    assert 250 <= resp.latency_ms <= 600 and resp.model_version == evon_mod.MODEL
    assert ask(c, "sharma ji paintees hazaar", timeout=100)[1].status == "TIMEOUT"


def test_sim_is_deterministic(tmp_path):
    assert ask(sim(tmp_path), "sharma ji paintees hazaar")[1].text == ask(sim(tmp_path), "sharma ji paintees hazaar")[1].text


def test_paraphrase_stub_keeps_the_template_text(tmp_path):
    c = sim(tmp_path)
    outs = {asyncio.run(c.complete(EvonRequest(purpose="paraphrase", prompt=f"TASK: x\nTEXT: {{PARTY}} ka {{AMOUNT}} jama {i}\nREWRITE:"))).text for i in range(30)}
    assert all("{PARTY} ka {AMOUNT} jama" in t for t in outs) and len(outs) > 1


def test_live_posts_chat_completions_and_caches(monkeypatch, tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"intent": "CREDIT_ENTRY", "slots": {}}'}}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(evon_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("EVON_TEST_KEY", "secret")
    c = EvonClient("live", {"url": "http://evon/v1/chat/completions", "model": "m", "key_env": "EVON_TEST_KEY"}, ProviderCache(tmp_path, "read_through"))
    req = EvonRequest(purpose="parse", prompt="hello")
    a, b = asyncio.run(c.complete(req)), asyncio.run(c.complete(req))
    assert a.status == b.status == "OK" and len(calls) == 1 and calls[0]["temperature"] == 0 and calls[0]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("handler,status", [(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")), "TIMEOUT"),
                                            (lambda r: httpx.Response(500), "ERROR"), (lambda r: httpx.Response(200, json={"oops": 1}), "ERROR")])
def test_live_failures_become_statuses_not_exceptions(monkeypatch, tmp_path, handler, status):
    real = httpx.AsyncClient
    monkeypatch.setattr(evon_mod.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    c = EvonClient("live", {"url": "http://evon/x", "model": "m", "key_env": "NOPE"}, ProviderCache(tmp_path, "read_through"))
    assert asyncio.run(c.complete(EvonRequest(purpose="parse", prompt="p"))).status == status
