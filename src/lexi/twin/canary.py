"""Canaries (C11 step 8): in operation one call per minute per cell from the current RTP, through the production path in LIVE mode, so every cell keeps
producing gold-labeled autonomous outcomes for M2. A CanaryRequest from the sentinel raises the rate of one cell for a while."""

from lexi.common.types import CanaryRequest


class Canaries:
    def __init__(self, cells: list[str], default_rate_per_min: float) -> None:
        self.default = default_rate_per_min
        self.rate = {c: default_rate_per_min for c in cells}
        self.until: dict[str, float] = {}  # cell -> time the raised rate ends
        self.credit = dict.fromkeys(cells, 0.0)  # fractional calls owed per cell

    def request(self, req: CanaryRequest, now: float) -> None:
        self.rate[req.cell] = max(self.default, req.rate_per_min)
        self.until[req.cell] = now + req.duration_s

    def due(self, now: float, dt_s: float, cap: float | None = None) -> list[str]:
        """Cells owed a canary call after `dt_s` seconds. `cap` (calls per minute, all cells together) comes from the credit governor."""
        for cell, until in list(self.until.items()):
            if now >= until:
                self.rate[cell] = self.default
                del self.until[cell]
        total = sum(self.rate.values())
        scale = 1.0 if cap is None or total <= cap else cap / total
        out = []
        for cell, rate in self.rate.items():
            self.credit[cell] += rate * scale / 60 * dt_s
            while self.credit[cell] >= 1.0:
                self.credit[cell] -= 1.0
                out.append(cell)
        return out
