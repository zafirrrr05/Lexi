"""All 38 message types of spec 8.2 as pydantic v2 models.

Conventions
  - Every message (class `Msg`) carries {schema_version, trace_id, ts}. Nested parts (class `Part`) do not.
  - Models forbid unknown fields, so a typo in a producer fails at the boundary, not in a consumer.
  - Slot values: amount = int rupees, date = ISO "YYYY-MM-DD", party = registry id, intent = catalog id.
  - Cell keys are "<lang>.<cqi>.<tier>", e.g. "hi.Q2.T1" (see Catalog.cell_key).
  - Fields marked (ext) are additive clarifications of 8.2 recorded in docs/DECISIONS.md D-002.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Lang = Literal["hi", "ta"]
Cqi = Literal["Q1", "Q2", "Q3"]
Tier = Literal["T1", "T2"]
Intent = Literal["CREDIT_ENTRY", "DEBIT_ENTRY", "PROMISE_TO_PAY", "RESCHEDULE_DUE"]
AsrStatus = Literal["OK", "EMPTY", "SILENCE", "UNAVAILABLE", "TIMEOUT"]
Mode = Literal["NEW_COMMAND", "PROBE_ANSWER", "UNDO_WINDOW", "CLOSING"]
DecisionKind = Literal["COMMIT", "PROBE", "REPROMPT", "ESCALATE", "REFUSE"]
ProbeKind = Literal["CONTRAST", "REASK", "READBACK", "INTENT_CONTRAST"]
OutcomeKind = Literal["CHOICE", "VALUE", "YES", "NO", "NO_WITH_VALUE", "UNCLEAR", "SILENCE", "UNDO", "CONTINUE"]
EntityType = Literal["AMOUNT", "DATE", "PARTY", "CUE"]
CellState = Literal["HEALTHY", "WATCH", "ALERT", "REVOKED"]
LabelTier = Literal["G", "A", "R"]
Value = int | str


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class Msg(Part):
    schema_version: int = 1
    trace_id: str = ""
    ts: float = 0.0


# --------------------------------------------------------------------------- audio, ASR, channel
class AudioFrame(Msg):
    session_id: str
    seq: int
    ts_ms: int
    sample_rate: int = 8000
    pcm_s16le: bytes  # 160 samples = 20 ms


class Word(Part):
    w: str
    t0: float
    t1: float
    conf: float | None
    lang: str


class Asr(Part):
    words: list[Word]
    text: str
    lang: str
    asr_status: AsrStatus


class ChannelProfile(Msg):
    turn_id: str
    snr_db: float
    bandwidth_hz95: float
    bandwidth_class: Literal["NB", "WB"]
    plc_fraction: float
    clip_fraction: float
    noise_nonstat: float
    speech_ms: float
    rate_proxy: float
    cqi: Cqi
    profiler_version: str
    short_speech: bool = False
    trailing_silence_ms: float = 1000.0  # (ext) silence at EOU; C3 step 5 needs it for the truncation rule


# --------------------------------------------------------------------------- dialogue and ledger view
class ProbeSpec(Part):
    probe_id: str
    kind: ProbeKind
    slot: str  # intent | party | amount | date | dup
    cell: str  # the stage-0 cell of the action (C7 R0)
    stage_index: int  # stage the answer will produce: 1 or 2
    options: list[str] = Field(default_factory=list)  # (ext) values offered, in spoken order


class Dialogue(Part):
    mode: Mode
    pending_probe: ProbeSpec | None
    probes_used: int
    turns_used: int
    reprompts_used: int
    entries_in_session: int


class Limits(Part):
    max_autonomous_amount_inr: int
    hard_max_inr: int
    duplicate_window_s: int
    max_entries_per_session: int
    max_probes: int
    max_turns: int
    max_reprompts: int


class PartyView(Part):
    id: str
    display: str
    aliases: list[str]
    ipk: str
    balance: int
    amount_hist: list[int]  # last 20
    last_entry_ts: float | None


class PendingEntry(Part):
    """This session's entries that are PENDING, or POSTED within the duplicate window (duplicate guard)."""

    entry_id: str
    intent: Intent
    party_id: str
    amount_inr: int | None
    date: str | None
    status: Literal["PENDING", "POSTED"]
    ts: float


class LedgerView(Msg):
    account: str
    parties: list[PartyView]
    today: str
    limits: Limits
    pending_entries: list[PendingEntry] = Field(default_factory=list)


class TurnInput(Msg):
    session_id: str
    turn_id: str
    asr: Asr
    profile: ChannelProfile
    dialogue: Dialogue
    ledger_view: LedgerView
    clock_ts: float
    long_utterance: bool = False  # (ext) C1 cut the utterance at max_utterance_s
    collect: bool = False  # (ext) the call was opened as a data-collection call: C7 runs it in COLLECT mode whatever the process mode is


# --------------------------------------------------------------------------- evidence (C3)
class Cue(Part):
    cls: str  # CREDIT DEBIT PROMISE RESCHEDULE NEG REPAIR YES NO RIGHT WRONG ORD1 ORD2 UNDO CONTINUE ...
    strength: float = 1.0


class Candidate(Part):
    value: Value
    p: float
    parse_id: int = 0


class EntityFeatures(Part):
    min_conf: float | None = None
    mean_conf: float | None = None
    n_lexemes: int = 0
    confusable_mass: float = 0.0
    repaired: bool = False
    truncated: bool = False


class Entity(Part):
    id: str  # E1, E2, ... in span order
    type: EntityType
    span: tuple[int, int]  # word index range [start, end)
    t0: float
    t1: float
    surface: str
    candidates: list[Candidate] = Field(default_factory=list)  # empty for CUE
    features: EntityFeatures = Field(default_factory=EntityFeatures)
    cue: Cue | None = None


class Lattice(Part):
    entities: list[Entity] = Field(default_factory=list)

    def get(self, entity_id: str) -> Entity | None:
        return next((e for e in self.entities if e.id == entity_id), None)

    def of_type(self, etype: EntityType) -> list[Entity]:
        return [e for e in self.entities if e.type == etype]


class EbFlags(Part):
    asr_failed: bool = False
    maybe_truncated: bool = False
    repair_present: bool = False
    conf_missing: bool = False
    mixed_script: bool = False
    long_utterance: bool = False


class DialogueCtx(Part):
    mode: Mode
    probes_used: int
    turns_used: int
    reprompts_used: int
    entries_in_session: int
    cell: str | None = None  # stage-0 cell carried by pending_probe (C1 step 5)


class PartyContext(Part):
    """Ledger slice C5 needs, attached per party candidate by C3 so C5 stays pure (C3 step 6)."""

    party_id: str
    display: str
    balance: int
    amount_hist: list[int]
    last_entry_ts: float | None
    session_recent: list[PendingEntry] = Field(default_factory=list)


class EvidenceBundle(Msg):
    eb_id: str
    session_id: str
    turn_id: str
    mode: Mode
    asr_status: AsrStatus
    lang: str
    words: list[Word]
    profile: ChannelProfile
    lattice: Lattice
    flags: EbFlags
    prior_context: dict[str, PartyContext]
    dialogue_ctx: DialogueCtx
    lexicon_version: str
    digest: str
    today: str  # (ext) ledger date, needed for date priors
    clock_ts: float  # (ext) injected clock at EOU


# --------------------------------------------------------------------------- parse, score, decide
class EvonInfo(Part):
    latency_ms: float
    model_version: str
    raw_digest: str


class ActionProposal(Msg):
    ap_id: str
    eb_id: str
    intent: Intent | None
    intent_candidates: list[Intent]
    slots: dict[str, str]  # slot name -> entity id. Never a value (I2).
    parse_source: Literal["DET", "EVON", "DET+EVON"]
    intent_agreement: bool
    missing_slots: list[str]
    evon: EvonInfo | None = None
    fail: str | None = None


class SlotPosterior(Part):
    entity_id: str | None  # None for the intent slot
    top: Value
    runner_up: Value | None
    p_top: float
    p_second: float


class SpFlags(Part):
    degraded: bool = False


class ScoredProposal(Msg):
    sp_id: str
    ap: ActionProposal
    eb: EvidenceBundle
    slots: dict[str, SlotPosterior]  # critical slots of the intent: intent, party, amount, date
    s: float
    s_raw: float
    stage_index: int
    scorer_version: str
    feature_vector: dict[str, float | None]
    feature_digest: str
    baseline_signals: dict[str, float | None]
    flags: SpFlags = Field(default_factory=SpFlags)
    intent: Intent | None = None  # (ext) chosen intent after INTENT_CONTRAST
    intent_agreement: bool = False  # (ext) may be raised by an INTENT_CONTRAST answer
    dup_cleared: bool = False  # (ext) DUP probe answered "new entry"
    ctx: DialogueCtx | None = None  # (ext) dialogue counters of the latest turn (answer turn at stages > 0)


class ProbeOutcome(Msg):
    probe_id: str
    kind: OutcomeKind
    chosen_index: int | None
    value_entity_id: str | None
    answer_conf: float | None
    latency_ms: float
    base_sp: ScoredProposal
    answer_eb: EvidenceBundle
    spec: ProbeSpec  # (ext) which probe this answers


class CertRef(Part):
    cert_id: str | None  # None when HEDGED
    rung: int
    lam: float
    hedged: bool = False


class BaselineShadow(Part):
    kind: Literal["COMMIT", "ESCALATE"]


class ProbeRequest(Part):
    base_sp: ScoredProposal
    stage_index: int
    reason: Literal["ABOVE_LAMBDA", "DUP", "INTENT"]


class Decision(Msg):
    decision_id: str
    kind: DecisionKind
    reason_code: str
    cell: str | None
    cert_ref: CertRef | None
    registry_version: int | None
    lease_expiry_seen: float | None
    scorer_version: str
    catalog_version: str
    baseline_shadow: BaselineShadow
    probe_request: ProbeRequest | None = None
    escalation_payload: dict[str, Any] | None = None


class CommitRequest(Msg):
    decision_id: str
    session_id: str
    turn_id: str
    audit_seq: int
    audit_hash: str
    intent: Intent
    party_id: str
    amount_inr: int | None
    date: str | None
    idempotency_key: str


class EscalationTicket(Msg):
    ticket_id: str
    decision_id: str
    session_id: str  # (ext) = call_id; HumanCommit needs it
    reason_code: str
    cell: str | None
    candidates: dict[str, list[tuple[Value, float]]]  # per slot with posteriors
    evidence_summary: dict[str, Any]


# --------------------------------------------------------------------------- speech
class Segment(Part):
    text: str
    lang: str
    voice: str
    prosody_hint: str | None = None


class Expects(Part):
    kind: Literal["NEW_COMMAND", "PROBE_ANSWER", "UNDO_WINDOW", "NONE"]
    probe_spec: ProbeSpec | None = None
    receipt_id: str | None = None
    facts: dict[str, Value] = Field(default_factory=dict)  # (ext) structured content just spoken: intent/party(id)/amount/date


class SpeechPlan(Msg):
    plan_id: str
    segments: list[Segment]
    expects: Expects
    barge_in_allowed: bool = True


class CallControl(Msg):
    session_id: str
    plan_id: str
    expects: Expects
    text: str  # (ext) what the caller hears; the console shows it, the Twin answers from it
    duration_ms: float  # (ext) playback length, so a caller knows when the system stopped talking


# --------------------------------------------------------------------------- ledger
class LedgerSignal(Msg):
    type: Literal["SESSION_OPEN", "SESSION_CLOSE", "RECEIPT_DELIVERED", "RECEIPT_FAILED"]
    session_id: str
    account_id: str | None = None
    entry_id: str | None = None


class Receipt(Msg):
    entry_id: str
    status: Literal["PENDING", "POSTED", "UNDONE", "REJECTED"]
    intent: Intent
    party_display: str
    amount_inr: int | None
    date: str | None
    balance_after_tentative: int | None
    session_id: str  # (ext) routes the receipt to the right call
    lang: str = "hi"  # (ext) call language for the spoken receipt


class UndoRequest(Msg):
    entry_id: str
    session_id: str


class HumanCommit(Msg):
    ticket_id: str
    session_id: str
    intent: Intent
    party_id: str
    amount_inr: int | None
    date: str | None
    operator_id: str


class HumanVerdict(Msg):
    ticket_id: str
    call_id: str
    values: dict[str, Value]
    provenance: Literal["human"] = "human"


# --------------------------------------------------------------------------- audit, health
class AuditRecord(Msg):
    seq: int
    prev_hash: str
    hash: str
    type: Literal["DECISION", "LEDGER_EVENT", "SESSION_EVENT", "REGISTRY_EVENT", "SENTINEL_EVENT"]
    payload: dict[str, Any]


class AuditQuery(Msg):
    seq: int
    hash: str


class HealthSignal(Msg):
    component: Literal["C9", "C12"]
    healthy: bool
    since_ts: float
    reason: str = ""


# --------------------------------------------------------------------------- labels and calibration
class GoldLabel(Msg):
    call_id: str
    script_id: str
    split: str
    scenario: str
    gold_intent: Intent
    gold_slots: dict[str, Value]  # party, amount, date as applicable
    spoken: list[str]  # (ext) tokens of the command utterance as spoken (canonical forms), for fitting C3 confusion tables
    tags: list[str]
    channel_params: dict[str, Any]
    seed: int


class LabeledOutcome(Msg):
    call_id: str
    cell: str | None
    decision_kind: DecisionKind
    committed_values: dict[str, Value]
    y_final: int  # 1 = material error against gold
    stage_index: int
    score_trajectory: list[float]
    label_tier: LabelTier
    autonomous: bool = True  # (ext) False for HumanCommit outcomes; M2 uses autonomous commits only


class CalibrationRecord(Msg):
    call_id: str
    cell: str  # stage-0 cell
    scores: list[float]  # s_0..s_J
    elig: list[int]  # el_0..el_J
    labels: list[int]  # y_0..y_J
    feature_vectors: list[dict[str, float | None]]
    scorer_version: str
    split: str
    scenario: str
    tags: list[str]


class CellStat(Part):
    n_records: int
    est_accept_by_rung: list[float]
    deficit_by_rung: list[int]
    gold_label_age_s: float | None


class CellStats(Msg):
    cells: dict[str, CellStat]


class TrainingData(Msg):
    kind: Literal["scorer", "confusion", "probe_models"]
    split: str
    records: list[dict[str, Any]]


class TestSplit(Msg):
    release_tag: str
    records: list[dict[str, Any]]


# --------------------------------------------------------------------------- authority and sentinel
class ScorerRelease(Msg):
    scorer_version: str
    bundle: Any = Field(exclude=True)  # loadable scorer bundle (C5); not serialised
    feature_schema_hash: str
    trained_on_window: dict[str, Any]


class LadderRung(Part):
    rung: int  # 0, 1, 2
    alpha: float
    lam: float
    cert_id: str
    n: int
    errors: int
    p: float
    delta: float


class Effective(Part):
    rung: int | None
    lam: float | None
    hedged: bool = False
    cert_id: str | None = None


class CellEntry(Part):
    state: CellState
    ladder: list[LadderRung]
    effective: Effective


class RegistrySnapshot(Msg):
    version: int
    lease_expiry: float  # the only field mutated in place (C6 step 8)
    scorer_version: str
    catalog_version: str
    cells: dict[str, CellEntry]


class Certificate(Part):
    """Persisted certificate (C6 step 5). Not a message; carried inside registry history and C13."""

    cert_id: str
    cell: str
    rung: int
    alpha: float
    delta: float
    lam: float
    n: int
    errors: int
    p_value: float
    scorer_version: str
    catalog_version: str
    calib_window: dict[str, Any]  # {from, to, hash}
    caller_model_version: str
    issued_at: float
    state: Literal["ACTIVE", "SUPERSEDED"]


class SentinelEvent(Msg):
    type: Literal["HEARTBEAT", "STATE_CHANGE"]
    cell: str | None = None
    from_state: CellState | None = None
    to_state: CellState | None = None
    detector: Literal["M1", "M2", "STALENESS"] | None = None
    value: float | None = None
    threshold: float | None = None
    shift_onset_ts: float | None = None


class CanaryRequest(Msg):
    cell: str
    rate_per_min: float
    duration_s: float


class ScenarioParams(Part):
    rate: float | None = None  # calls per minute
    speed: float | None = None
    seed: int | None = None
    n_calls: int | None = None  # (ext) a finite batch instead of a stream (K33: fresh shift-test and live-run batches)
    split: str | None = None  # (ext) pin the split of a batch
    cell: str | None = None  # (ext) pin the cell of a batch


class ScenarioCommand(Msg):
    action: Literal["START", "STOP", "INJECT"]
    scenario: Literal["S0", "S1", "S2", "S3", "S4", "S5", "S6"]
    params: ScenarioParams = Field(default_factory=ScenarioParams)


class EvalReport(Msg):
    release_tag: str
    metrics: dict[str, Any]
    slo_table: list[dict[str, Any]]
    passed: bool


# --------------------------------------------------------------------------- provider messages
class PrismaStream(Msg):
    """Downstream Prisma event (X02). Upstream is raw PCM."""

    session_id: str
    kind: Literal["partial", "final", "error"]
    words: list[Word]
    text: str
    lang: str
    t0_ms: int
    t1_ms: int
    status: AsrStatus = "OK"


class EvonRequest(Msg):
    purpose: Literal["parse", "paraphrase"]
    prompt: str
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    max_tokens: int = 96
    temperature: float = 0.0
    timeout_ms: int = 800


class EvonResponse(Msg):
    status: Literal["OK", "TIMEOUT", "ERROR"]
    text: str | None
    latency_ms: float
    model_version: str


class TtsRequest(Msg):
    text: str
    lang: str
    voice: str
    rate: float = 1.0
    pitch_semitones: float = 0.0
    prosody_hint: str | None = None


class TtsAudio(Msg):
    pcm_s16le: bytes
    sample_rate: int
    duration_ms: float
    voice: str
    cached: bool
    word_timings: list[tuple[str, float, float]] | None = None  # (ext, sim only) token, t0, t1 seconds
