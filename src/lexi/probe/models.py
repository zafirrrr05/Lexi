"""Open probe contexts (C8 step 5): what the planner remembers between asking a question and hearing the answer."""

from dataclasses import dataclass

from lexi.common.types import ProbeSpec, ScoredProposal

EXPIRY_S = 120.0  # a context is dropped after this long, on session close, or when the action reaches a terminal outcome


@dataclass(slots=True)
class OpenProbe:
    session_id: str
    base_sp: ScoredProposal
    spec: ProbeSpec
    issued_at: float
