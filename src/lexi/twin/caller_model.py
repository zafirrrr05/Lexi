"""Caller behaviour model CM-v1 (C11 step 6). The caller knows the truth: it answers a probe correctly with probability 1 - eps_u, acquiesces to a wrong
read-back with probability kappa, stays silent with probability `silence`, restates values, and sometimes barges in. The parameters are versioned and
stamped into every certificate (C6), so a certificate says which caller it was earned against."""

import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from lexi.common.types import Expects
from lexi.twin.scripts import AccountSpec, PartySpec, Script, recommand, restate
from lexi.twin.verbalize import SpokenToken, answer_tokens, cue_tokens

CUE_OF = {"CREDIT_ENTRY": "CREDIT", "DEBIT_ENTRY": "DEBIT", "PROMISE_TO_PAY": "PROMISE", "RESCHEDULE_DUE": "RESCHEDULE"}


@dataclass(frozen=True)
class CallerParams:
    eps_u: float = 0.05  # answers a probe wrongly
    kappa: float = 0.25  # says yes to a wrong read-back (or stays quiet about a wrong receipt)
    silence: float = 0.03
    barge_in: float = 0.05
    version: str = "CM-v1"

    @property
    def stamp(self) -> str:
        return f"{self.version}:eps_u={self.eps_u}:kappa={self.kappa}:silence={self.silence}:barge_in={self.barge_in}"


@dataclass(frozen=True)
class Reply:
    tokens: list[SpokenToken]  # empty = the caller says nothing
    barge_in: bool = False


class Caller:
    def __init__(self, script: Script, account: AccountSpec, today: date, rng: random.Random, params: CallerParams | None = None) -> None:
        self.script, self.today, self.rng, self.p = script, today, rng, params or CallerParams()
        self.party: PartySpec = next(x for x in account.parties if x.id == script.party_id)
        self.spoke = False

    def _values(self, *slots: str) -> list[SpokenToken]:
        return restate(self.script, self.party, slots, self.today, self.rng)

    def _agrees(self, facts: dict[str, Any]) -> bool:
        return all(str(self.script.gold.get(k)) == str(v) for k, v in facts.items() if k in self.script.gold)

    def respond(self, expects: Expects) -> Reply:
        """What the caller says after the system spoke, given what the system expects."""
        r = self.rng
        if expects.kind == "NONE":
            return Reply([])
        if expects.kind == "NEW_COMMAND":
            first, self.spoke = not self.spoke, True
            return Reply(list(self.script.tokens) if first else recommand(self.script, self.party, self.today, r))
        if r.random() < self.p.silence:
            return Reply([])
        barge = r.random() < self.p.barge_in
        if expects.kind == "UNDO_WINDOW":  # the receipt is wrong: say so, unless the caller lets it pass
            wrong = not self._agrees(expects.facts)
            return Reply(answer_tokens("WRONG", self.script.lang, r) if wrong and r.random() > self.p.kappa else [], barge)
        spec = expects.probe_spec
        assert spec is not None
        slip = r.random() < self.p.eps_u
        return Reply(self._answer(expects, slip), barge)

    def _answer(self, expects: Expects, slip: bool) -> list[SpokenToken]:
        spec, lang, r = expects.probe_spec, self.script.lang, self.rng
        assert spec is not None
        if spec.kind == "READBACK":
            if self._agrees(expects.facts):
                return answer_tokens("NO" if slip else "YES", lang, r)
            if r.random() < self.p.kappa:
                return answer_tokens("YES", lang, r)  # acquiesces to a wrong read-back
            wrong = next((k for k, v in expects.facts.items() if k in self.script.gold and str(self.script.gold[k]) != str(v)), None)
            tokens = answer_tokens("NO", lang, r)
            return tokens + self._values(wrong) if wrong in ("party", "amount", "date") and r.random() < 0.5 else tokens
        if spec.kind == "REASK":
            return self._values(spec.slot)
        gold = str(self.script.gold.get(spec.slot, "new" if spec.slot == "dup" else ""))
        if spec.slot == "dup":  # every call is a new entry
            return answer_tokens("SAME" if slip else "NEW", lang, r)
        if gold in spec.options:
            index = spec.options.index(gold)
            if slip and len(spec.options) == 2:
                index = 1 - index
            if spec.kind == "INTENT_CONTRAST" and r.random() < 0.6:
                return cue_tokens(CUE_OF[spec.options[index]], lang, r)
            if r.random() < 0.5:
                return answer_tokens("ORD2" if index else "ORD1", lang, r)
        if spec.slot == "intent":
            return cue_tokens(CUE_OF[self.script.intent], lang, r)
        return self._values(spec.slot)
