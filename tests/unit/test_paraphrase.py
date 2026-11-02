import asyncio

from lexi.common.types import EvonResponse
from lexi.twin.paraphrase import paraphrase, paraphrase_ok

T = "{PARTY} ka {AMOUNT} jama kar do"


def evon_returning(text, status="OK"):
    async def call(req):
        return EvonResponse(status=status, text=text, latency_ms=1.0, model_version="x")
    return call


def test_each_placeholder_exactly_once_and_unchanged():
    assert paraphrase_ok(T, "suniye {PARTY} ka {AMOUNT} jama kar do")
    assert not paraphrase_ok(T, "{PARTY} ka jama kar do")  # lost a placeholder
    assert not paraphrase_ok(T, "{PARTY} {PARTY} ka {AMOUNT} jama")  # duplicated
    assert not paraphrase_ok(T, "{PARTY} ka {AMOUNT} {DATE} jama")  # invented


def test_rejected_or_failed_paraphrase_returns_the_template():
    assert asyncio.run(paraphrase(T, evon_returning("accha, {PARTY} ka {AMOUNT} jama kar do"))) == "accha, {PARTY} ka {AMOUNT} jama kar do"
    assert asyncio.run(paraphrase(T, evon_returning("{AMOUNT} jama"))) == T
    assert asyncio.run(paraphrase(T, evon_returning(None, "TIMEOUT"))) == T
