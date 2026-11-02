"""C4 Action Parser: EvidenceBundle -> ActionProposal. Slots hold entity ids, never values (I2)."""

from collections.abc import Awaitable, Callable

from lexi.common.catalog import Catalog
from lexi.common.hashing import digest
from lexi.common.ids import det_id
from lexi.common.types import ActionProposal, EvidenceBundle, EvonInfo, EvonRequest, EvonResponse, Intent
from lexi.parser.evon_parse import build_prompt, load_template, validate
from lexi.parser.grammar import Tier1, tier1
from lexi.parser.sign_guard import guard

EvonCall = Callable[[EvonRequest], Awaitable[EvonResponse]]


class Parser:
    def __init__(self, catalog: Catalog, evon: EvonCall | None, *, timeout_ms: int = 800) -> None:
        self.catalog, self.evon, self.timeout_ms = catalog, evon, timeout_ms
        self.template, self.schema = load_template()

    def _ap(self, eb: EvidenceBundle, t1: Tier1, *, intent: Intent | None, slots: dict[str, str], source: str,
            evon_intent: Intent | None = None, evon: EvonInfo | None = None, fail: str | None = None) -> ActionProposal:
        agree, candidates = guard(intent, t1.strong and t1.intent == intent, evon_intent, evon is not None) if intent else (False, [])
        required = self.catalog.actions[intent].slots if intent else []
        return ActionProposal(
            trace_id=eb.trace_id, ts=eb.ts, ap_id=det_id("AP", eb.eb_id, intent, slots, fail), eb_id=eb.eb_id, intent=intent,
            intent_candidates=candidates, slots=slots, parse_source=source,
            intent_agreement=agree, missing_slots=[s for s in required if s not in slots], evon=evon, fail=fail)

    def _early_fail(self, eb: EvidenceBundle) -> str | None:
        return "asr_failed" if eb.flags.asr_failed else "conf_missing" if eb.flags.conf_missing else None

    def _tier1_ap(self, eb: EvidenceBundle, t1: Tier1) -> ActionProposal:
        if t1.klass == "UNAMBIGUOUS":
            return self._ap(eb, t1, intent=t1.intent, slots=t1.slots, source="DET")
        return self._ap(eb, t1, intent=None, slots={}, source="DET", fail="no_intent" if t1.klass == "FAIL" else "parse_ambiguous")

    def parse_tier1(self, eb: EvidenceBundle) -> ActionProposal:
        if (fail := self._early_fail(eb)):
            return self._ap(eb, Tier1("FAIL"), intent=None, slots={}, source="DET", fail=fail)
        return self._tier1_ap(eb, tier1(eb, self.catalog))

    async def parse(self, eb: EvidenceBundle) -> ActionProposal:
        if (fail := self._early_fail(eb)):
            return self._ap(eb, Tier1("FAIL"), intent=None, slots={}, source="DET", fail=fail)
        t1 = tier1(eb, self.catalog)
        if t1.klass == "UNAMBIGUOUS" or self.evon is None:
            return self._tier1_ap(eb, t1)
        resp = await self.evon(EvonRequest(purpose="parse", prompt=build_prompt(self.template, eb, self.catalog), schema=self.schema,
                                           max_tokens=96, temperature=0.0, timeout_ms=self.timeout_ms))
        if resp.status != "OK":
            return self._ap(eb, t1, intent=None, slots={}, source="DET", fail="evon_timeout")
        parsed = validate(resp.text, eb, self.catalog)
        if parsed is None:
            return self._ap(eb, t1, intent=None, slots={}, source="DET", fail="evon_invalid")
        e_intent, e_slots = parsed
        info = EvonInfo(latency_ms=resp.latency_ms, model_version=resp.model_version, raw_digest=digest(resp.text))
        intent = t1.intent or e_intent  # a lone strong cue stays; Evon only picks when the cues are absent, weak or in conflict
        slots = {**t1.slots, **{k: v for k, v in e_slots.items() if k not in t1.slots}} if intent == e_intent else dict(t1.slots)
        return self._ap(eb, t1, intent=intent, slots=slots, source="DET+EVON" if t1.candidates else "EVON", evon_intent=e_intent, evon=info)
