"""RC6: over 100,000 random evidence bundles, with an Evon that answers adversarially, no ActionProposal slot holds anything but an entity of its own bundle."""

import asyncio
import json
import random

from tests.helpers import EVIDENCE, turn

from lexi.common.catalog import load_catalog
from lexi.common.types import EvonRequest, EvonResponse
from lexi.parser.parser import Parser

N = 100_000
CAT = load_catalog()
WORDS = "sharma ji ka verma gupta paanch sau hazaar paintees do teen lakh jama udhaar likh denge kal parso agle hafte tareekh xyzzy 250 nahi".split()
TYPE = {"party": "PARTY", "amount": "AMOUNT", "date": "DATE"}


def test_no_slot_outside_the_bundle_in_100k_random_bundles():
    rng = random.Random(20261004)
    intents = [*CAT.actions, "HACK"]

    async def hostile(req: EvonRequest) -> EvonResponse:
        pick = lambda: rng.choice([f"E{rng.randint(1, 9)}", "E99", "", "35000", "party"])  # noqa: E731
        body = {"intent": rng.choice(intents), "slots": {k: pick() for k in rng.sample(["party", "amount", "date", "x"], rng.randint(0, 4))}}
        return EvonResponse(status="OK", text=json.dumps(body) if rng.random() < 0.9 else "{bad", latency_ms=1.0, model_version="hostile")

    async def go() -> tuple[int, int]:
        parser, bad, accepted = Parser(CAT, hostile), 0, 0
        for _ in range(N):
            eb = EVIDENCE.build(turn(" ".join(rng.choices(WORDS, k=rng.randint(1, 12))), conf=rng.choice([0.5, 0.9, 0.99])))
            ap = await parser.parse(eb)
            bad += ap.intent not in (None, *CAT.actions)
            bad += sum((ent := eb.lattice.get(eid)) is None or ent.type != TYPE[slot] for slot, eid in ap.slots.items())
            accepted += "EVON" in ap.parse_source and bool(ap.slots)
        return bad, accepted

    bad, accepted = asyncio.run(go())
    assert bad == 0 and accepted > N // 200  # the adversary got slots through often enough for the check to mean something
