"""Builders shared by tests: a ledger view, a turn from plain text, an evidence bundle from the real C3."""

from lexi.common.types import (
    Asr,
    ChannelProfile,
    Dialogue,
    LedgerView,
    Limits,
    PartyView,
    TurnInput,
    Word,
)
from lexi.evidence.builder import Evidence

LIMITS = Limits(max_autonomous_amount_inr=25000, hard_max_inr=100000, duplicate_window_s=120, max_entries_per_session=10,
                max_probes=2, max_turns=8, max_reprompts=2)
PARTIES = [
    PartyView(id="P01", display="Sharma Kirana", aliases=["sharma ji", "sharma"], ipk="sarma kirana", balance=1200, amount_hist=[500, 1000], last_entry_ts=None),
    PartyView(id="P02", display="Verma Traders", aliases=["verma"], ipk="verma traders", balance=0, amount_hist=[], last_entry_ts=None),
    PartyView(id="P03", display="Gupta Stores", aliases=["gupta"], ipk="gupta storis", balance=300, amount_hist=[300], last_entry_ts=None),
]
VIEW = LedgerView(account="A1", parties=PARTIES, today="2026-10-09", limits=LIMITS)


def profile(cqi: str = "Q2", snr: float = 12.0, silence_ms: float = 800.0) -> ChannelProfile:
    return ChannelProfile(turn_id="t1", snr_db=snr, bandwidth_hz95=3000.0, bandwidth_class="NB", plc_fraction=0.0, clip_fraction=0.0,
                          noise_nonstat=1.0, speech_ms=2000.0, rate_proxy=3.0, cqi=cqi, profiler_version="prof-1",  # type: ignore[arg-type]
                          trailing_silence_ms=silence_ms)


def turn(text: str, *, mode: str = "NEW_COMMAND", conf: float | None = 0.9, lang: str = "hi", cqi: str = "Q2",
         silence_ms: float = 800.0, status: str = "OK", view: LedgerView = VIEW) -> TurnInput:
    words = [Word(w=x, t0=i * 0.3, t1=i * 0.3 + 0.25, conf=conf, lang=lang) for i, x in enumerate(text.split())]
    return TurnInput(session_id="s1", turn_id="t1", asr=Asr(words=words, text=text, lang=lang, asr_status=status),  # type: ignore[arg-type]
                     profile=profile(cqi, silence_ms=silence_ms),
                     dialogue=Dialogue(mode=mode, pending_probe=None, probes_used=0, turns_used=1, reprompts_used=0, entries_in_session=0),  # type: ignore[arg-type]
                     ledger_view=view, clock_ts=0.0)


EVIDENCE = Evidence.load()


# ---- decision plane: real C3 -> real C4 (tier 1) -> real C5, so tests start from realistic objects
from lexi.common.catalog import load_catalog  # noqa: E402
from lexi.common.types import ProbeOutcome, ProbeSpec, ScoredProposal  # noqa: E402
from lexi.parser.parser import Parser  # noqa: E402
from lexi.risk.probe_update import ProbeParams  # noqa: E402
from lexi.risk.scorer import RiskEngine  # noqa: E402

CATALOG = load_catalog()
PARSER = Parser(CATALOG, None)
ENGINE = RiskEngine(CATALOG, None, ProbeParams())


def make_sp(text: str = "sharma ji ka paintees hazaar jama kar do", *, conf: float | None = 0.95, cqi: str = "Q2", engine: RiskEngine = ENGINE,
            **kw) -> ScoredProposal:
    eb = EVIDENCE.build(turn(text, conf=conf, cqi=cqi, **kw))
    return engine.score(eb, PARSER.parse_tier1(eb))


def make_outcome(base: ScoredProposal, kind: str, spec_kind: str, slot: str, *, options: list[str] | None = None, chosen_index: int | None = None,
                 answer_text: str = "haan", value_text: str | None = None, conf: float = 0.9) -> ProbeOutcome:
    """An answered probe: the answer EB comes from the real C3 in PROBE_ANSWER mode."""
    answer_eb = EVIDENCE.build(turn(value_text or answer_text, mode="PROBE_ANSWER", conf=conf))
    ent = next((e for e in answer_eb.lattice.entities if e.type in ("AMOUNT", "PARTY", "DATE")), None)
    spec = ProbeSpec(probe_id="pr1", kind=spec_kind, slot=slot, cell="hi.Q2.T1", stage_index=base.stage_index + 1, options=options or [])  # type: ignore[arg-type]
    return ProbeOutcome(probe_id="pr1", kind=kind, chosen_index=chosen_index, value_entity_id=ent.id if ent and kind in ("VALUE", "NO_WITH_VALUE") else None,  # type: ignore[arg-type]
                        answer_conf=conf, latency_ms=800.0, base_sp=base, answer_eb=answer_eb, spec=spec)


# ---- gate: registry snapshot and DecideInput builders
from lexi.common.types import CellEntry, Effective, LadderRung, RegistrySnapshot  # noqa: E402
from lexi.gate.decide import DecideInput  # noqa: E402

CELL = "hi.Q2.T1"
OK_TEXT = "sharma ji ka do hazaar jama kar do"


def registry(lam: float | None = 0.2, *, cell: str = CELL, lease: float = 100.0, scorer: str = "bootstrap-naive", ts: float = 0.0,
             hedged: bool = False, state: str = "HEALTHY") -> RegistrySnapshot:
    ladder = [LadderRung(rung=0, alpha=0.05, lam=lam, cert_id="C-1", n=400, errors=3, p=0.01, delta=0.025)] if lam is not None and not hedged else []
    eff = Effective(rung=0 if lam is not None else None, lam=lam, hedged=hedged, cert_id=None if hedged or lam is None else "C-1")
    return RegistrySnapshot(version=7, lease_expiry=lease, scorer_version=scorer, catalog_version=CATALOG.version, ts=ts,
                            cells={cell: CellEntry(state=state, ladder=ladder, effective=eff)})  # type: ignore[arg-type]


def decide_input(sp: ScoredProposal, *, reg: RegistrySnapshot | None = None, mode: str = "LIVE", now: float = 10.0, audit_ok: bool = True,
                 ledger_ok: bool = True, lambda_min: float | None = None, theta_b: float = 0.5) -> DecideInput:
    return DecideInput(sp=sp, mode=mode, now=now, registry=reg, registry_max_age_s=604800.0, scorer_version_active="bootstrap-naive",  # type: ignore[arg-type]
                       audit_healthy=audit_ok, ledger_healthy=ledger_ok, lambda_min=lambda_min, theta_b=theta_b)
