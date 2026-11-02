"""Scenario library (C11 step 5) and the per-call plan: script, account, channel, voice, seed."""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from lexi.common.hashing import digest
from lexi.common.splits import split_of
from lexi.twin.channel import ChannelParams, channel_for_cqi
from lexi.twin.scripts import CARRIERS, AccountSpec, Script, make_script
from lexi.twin.voices import Voice, train_voices, unseen_voices

RTP_CQI = (("Q1", 0.4), ("Q2", 0.4), ("Q3", 0.2))  # C11 step 5: the Reference Traffic Profile
MONSOON = ChannelParams(codec="mulaw", noise="babble", snr_db=-10.0, loss_pct=5.0, rt60_s=0.3, clip=False)
MAX_TRIES = 400  # a pinned split (Valid is 10% of scripts) is found by drawing again


@dataclass(frozen=True)
class Scenario:
    name: str
    label: str
    stratum: str = "rtp"
    channel: ChannelParams | None = None  # fixed channel for every call
    codec: str | None = None  # replaces the codec of the drawn channel
    unseen_voices: bool = False
    unseen_parties: bool = False


SCENARIOS: dict[str, Scenario] = {s.name: s for s in (
    Scenario("S0", "RTP"),
    Scenario("S1", "MONSOON", channel=MONSOON),
    Scenario("S2", "CODEC_SWAP", codec="amr"),
    Scenario("S3", "NEW_SPEAKERS", unseen_voices=True),
    Scenario("S4", "NEW_PARTIES", unseen_parties=True),
    Scenario("S5", "MINIMAL_PAIRS", stratum="minimal_pair"),
    Scenario("S6", "NULL"),  # the same traffic as S0, run long to measure false alarms
)}


@dataclass(frozen=True)
class CallPlan:
    call_id: str
    scenario: str
    seed: int
    split: str
    script: Script
    account: AccountSpec
    channel: ChannelParams
    voice: Voice


def plan_call(rng: random.Random, scenario: Scenario, accounts: Mapping[str, Sequence[AccountSpec]], today: date, *,
              cell: str | None = None, split: str | None = None, templates: Mapping[tuple[str, str], Sequence[str]] = CARRIERS) -> CallPlan:
    """One call. `accounts` is keyed "train" / "unseen"; `cell` pins language, quality bucket and tier, `split` pins the script's split (S5 never needs a cell)."""
    pool = accounts["unseen" if scenario.unseen_parties else "train"]
    if cell is not None:
        lang, cqi, tier = cell.split(".")
    else:
        lang = rng.choice(sorted({a.lang for a in pool}))
        cqi, tier = rng.choices([c for c, _ in RTP_CQI], [w for _, w in RTP_CQI])[0], None
    for _ in range(MAX_TRIES):
        script = make_script(rng, pool, lang, scenario.stratum, today, templates, tier)
        if split is None or split_of(script.script_id) == split:
            break
    else:
        raise RuntimeError(f"no script of split {split} for {cell} in {MAX_TRIES} draws")
    channel = scenario.channel or channel_for_cqi(cqi, rng)
    if scenario.codec:
        channel = replace(channel, codec=scenario.codec)
    voice = rng.choice(unseen_voices() if scenario.unseen_voices else train_voices())
    seed = rng.getrandbits(32)
    account = next(a for a in pool if a.account == script.account)
    return CallPlan(f"call-{digest([seed, script.script_id])[:10]}", scenario.name, seed, split_of(script.script_id), script, account, channel, voice)
