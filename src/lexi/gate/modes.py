"""Gate modes (C7) are process configuration, never runtime messages. HealthState is what the C9/C12 health signals (K30, K31) write."""

from dataclasses import dataclass
from typing import Literal

GateMode = Literal["LIVE", "COLLECT", "COLD_START"]


@dataclass
class HealthState:
    audit_healthy: bool = True
    ledger_healthy: bool = True
