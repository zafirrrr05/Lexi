"""C8 planner: Decision / Receipt -> SpeechPlan, and the caller's answer -> ProbeOutcome or UndoRequest.

What is said is a pure function of the decision and the scored proposal; the planner only keeps the open probe contexts (C8 step 5)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from lexi.common.catalog import Catalog
from lexi.common.clock import Clock
from lexi.common.ids import det_id
from lexi.common.types import (
    Decision,
    EvidenceBundle,
    Expects,
    ProbeOutcome,
    ProbeSpec,
    Receipt,
    ScoredProposal,
    Segment,
    SpeechPlan,
    UndoRequest,
    Value,
)
from lexi.probe.answers import answer_conf, is_undo, parse
from lexi.probe.menu import Probe, eligible
from lexi.probe.models import EXPIRY_S, OpenProbe
from lexi.probe.select import choose_probe
from lexi.probe.verbalizer import PROMPTS_DIR, amount_phrase, spoken_date
from lexi.risk.probe_update import ProbeParams


class Planner:
    def __init__(self, catalog: Catalog, probe: ProbeParams, clock: Clock, *, prompts_dir: Path | None = None, ablate: frozenset[str] = frozenset()) -> None:
        self.catalog, self.params, self.clock, self.ablate = catalog, probe, clock, ablate
        self.dir = prompts_dir or PROMPTS_DIR
        # a missing prompts file fails here, at startup, rather than mid-call (P1)
        self.tpl = {lang: yaml.safe_load((self.dir / f"{lang}.yaml").read_text(encoding="utf-8")) for lang in catalog.languages}
        self._open: dict[str, OpenProbe] = {}  # by session: a call is one action, so one open probe
        self._receipts: dict[str, tuple[str, float]] = {}  # session -> (entry_id, issued_at) while its undo window is open

    # ---- speaking
    def plan(self, session_id: str, decision: Decision, sp: ScoredProposal) -> SpeechPlan:
        t = self._t(sp.eb.lang)
        if decision.kind == "PROBE":
            return self._probe(session_id, decision, sp, t)
        self.close_session(session_id)  # every other kind ends or restarts the action
        if decision.kind == "REPROMPT":
            slot = sp.ap.missing_slots[0] if decision.reason_code == "MISSING_SLOT" and sp.ap.missing_slots else "generic"
            return self._plan(decision.decision_id, sp, t, t["reprompt"].get(slot) or t["reprompt"]["generic"], Expects(kind="NEW_COMMAND"))
        text = {"ESCALATE": t["escalate"], "REFUSE": t["refuse"]}.get(decision.kind, t["closing"])  # COMMIT only reaches here as COLLECT_STOP
        return self._plan(decision.decision_id, sp, t, text, Expects(kind="NONE"), barge_in=decision.kind == "COMMIT")

    def plan_receipt(self, r: Receipt) -> SpeechPlan:
        t = self._t(r.lang)
        self.close_session(r.session_id)
        if r.status == "POSTED":
            return SpeechPlan(plan_id=det_id("PLAN", r.entry_id, r.status), segments=[], expects=Expects(kind="NONE"))
        if r.status != "PENDING":
            text = t["undone"] if r.status == "UNDONE" else t["failed"]
            return SpeechPlan(plan_id=det_id("PLAN", r.entry_id, r.status), segments=[Segment(text=text, lang=r.lang, voice=t["voice"])],
                              expects=Expects(kind="NONE"), barge_in_allowed=False)
        amount, echo = amount_phrase(r.amount_inr, r.lang, self.dir) if r.amount_inr is not None else ("", False)
        text = t["receipt"][r.intent].format(party=r.party_display, amount=amount, date=spoken_date(r.date, r.lang, self.dir) if r.date else "")
        self._receipts[r.session_id] = (r.entry_id, self.clock.now())
        facts: dict[str, Value] = {"intent": r.intent, "party": r.party_display} | ({"amount": r.amount_inr} if r.amount_inr is not None else {}) | (
            {"date": r.date} if r.date else {})
        return SpeechPlan(plan_id=det_id("PLAN", r.entry_id, r.status), segments=[Segment(text=text, lang=r.lang, voice=t["voice"],
                          prosody_hint="digitwise" if echo else None)], expects=Expects(kind="UNDO_WINDOW", receipt_id=r.entry_id, facts=facts))

    # ---- listening
    def on_answer(self, eb: EvidenceBundle) -> ProbeOutcome | UndoRequest | None:
        """PROBE_ANSWER -> ProbeOutcome. UNDO_WINDOW -> UndoRequest, or None (CONTINUE): the turn is then a new command (C8 step 3)."""
        sid = eb.session_id
        if eb.mode == "UNDO_WINDOW":
            held = self._receipts.pop(sid, None) if is_undo(eb) else None
            return UndoRequest(trace_id=eb.trace_id, ts=eb.clock_ts, entry_id=held[0], session_id=sid) if held else None
        op = self._open.pop(sid, None)
        if op is None or eb.mode != "PROBE_ANSWER":
            return None
        a = parse(eb, op.spec)
        return ProbeOutcome(trace_id=eb.trace_id, ts=eb.clock_ts, probe_id=op.spec.probe_id, kind=a.kind, chosen_index=a.chosen_index,
                            value_entity_id=a.value_entity_id, answer_conf=answer_conf(eb), latency_ms=max(eb.clock_ts - op.issued_at, 0.0) * 1000,
                            base_sp=op.base_sp, answer_eb=eb, spec=op.spec)

    def expire(self, now: float) -> None:
        self._open = {k: v for k, v in self._open.items() if now - v.issued_at <= EXPIRY_S}
        self._receipts = {k: v for k, v in self._receipts.items() if now - v[1] <= EXPIRY_S}

    def close_session(self, session_id: str) -> None:
        self._open.pop(session_id, None)
        self._receipts.pop(session_id, None)

    # ---- internals
    def _t(self, lang: str) -> dict[str, Any]:
        """The call language's templates; an unknown language falls back to the first catalog language rather than staying silent."""
        return self.tpl.get(lang) or self.tpl[self.catalog.languages[0]]  # type: ignore[no-any-return]

    def _plan(self, plan_key: str, sp: ScoredProposal, t: Mapping[str, Any], text: str, expects: Expects, *, barge_in: bool = True,
              hint: str | None = None) -> SpeechPlan:
        return SpeechPlan(trace_id=sp.trace_id, ts=self.clock.now(), plan_id=det_id("PLAN", plan_key),
                          segments=[Segment(text=text, lang=t["lang"], voice=t["voice"], prosody_hint=hint)], expects=expects, barge_in_allowed=barge_in)

    def _say(self, sp: ScoredProposal, slot: str, value: Value, lang: str) -> tuple[str, bool]:
        if slot == "party":
            ctx = sp.eb.prior_context.get(str(value))
            return (ctx.display if ctx else str(value)), False
        if slot == "amount":
            return amount_phrase(int(value), lang, self.dir)
        return spoken_date(str(value), lang, self.dir), False

    def _probe(self, session_id: str, decision: Decision, sp: ScoredProposal, t: Mapping[str, Any]) -> SpeechPlan:
        req = decision.probe_request
        assert req is not None  # a PROBE decision always carries its request (C7 R7)
        probe = choose_probe(sp, eligible(sp, req.reason, yes_no_only="yes_no_only" in self.ablate), self.params)
        spec = ProbeSpec(probe_id=det_id("PRB", sp.sp_id), kind=probe.kind, slot=probe.slot, cell=decision.cell or "", stage_index=req.stage_index,
                         options=list(probe.options))
        text, echo, facts = self._probe_text(sp, probe, t)
        self._open[session_id] = OpenProbe(session_id, sp, spec, self.clock.now())
        return self._plan(decision.decision_id, sp, t, text, Expects(kind="PROBE_ANSWER", probe_spec=spec, facts=facts), hint="digitwise" if echo else None)

    def _probe_text(self, sp: ScoredProposal, probe: Probe, t: Mapping[str, Any]) -> tuple[str, bool, dict[str, Value]]:
        lang, tpl = t["lang"], t["probe"]
        if probe.kind == "INTENT_CONTRAST":
            return tpl["INTENT_CONTRAST"], False, {}
        if probe.kind == "REASK":
            return tpl["REASK"][probe.slot], False, {}
        if probe.kind == "CONTRAST" and probe.slot != "dup":
            (o1, e1), (o2, e2) = (self._say(sp, probe.slot, v, lang) for v in probe.options)
            return tpl["CONTRAST"][probe.slot].format(o1=o1, o2=o2), e1 or e2, {}
        if probe.kind == "CONTRAST":
            return tpl["CONTRAST"]["dup"], False, {}
        intent = sp.intent or sp.ap.intent
        assert intent is not None
        say = {k: self._say(sp, k, s.top, lang) for k, s in sp.slots.items() if k != "intent"}
        facts: dict[str, Value] = {"intent": intent, **{k: s.top for k, s in sp.slots.items() if k != "intent"}}
        facts["party"] = say["party"][0]  # facts name the party as it was spoken (Receipt has no id, so receipts do the same)
        text = tpl["READBACK"]["dup" if probe.slot == "dup" else intent].format(**{k: v[0] for k, v in say.items()})
        return text, any(v[1] for v in say.values()), facts
