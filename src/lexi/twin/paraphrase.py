"""Carrier-phrase paraphrase through Evon (C11 step 1). A paraphrase is accepted only if every placeholder of the template
appears exactly once and unchanged, so the verbalizer can insert values afterwards and gold stays exact."""

from collections.abc import Awaitable, Callable

from lexi.common.types import EvonRequest, EvonResponse

PLACEHOLDERS = ("{PARTY}", "{AMOUNT}", "{DATE}")
EvonCall = Callable[[EvonRequest], Awaitable[EvonResponse]]


def paraphrase_ok(template: str, candidate: str) -> bool:
    return all(candidate.count(p) == (1 if p in template else 0) for p in PLACEHOLDERS)


async def paraphrase(template: str, evon: EvonCall) -> str:
    prompt = f"TASK: Rewrite the sentence with the same meaning and language. Keep {{PARTY}} {{AMOUNT}} {{DATE}} unchanged, each exactly once.\nTEXT: {template}\nREWRITE:"
    resp = await evon(EvonRequest(purpose="paraphrase", prompt=prompt, max_tokens=96, temperature=0.0, timeout_ms=800))
    candidate = (resp.text or "").strip()
    return candidate if resp.status == "OK" and paraphrase_ok(template, candidate) else template
