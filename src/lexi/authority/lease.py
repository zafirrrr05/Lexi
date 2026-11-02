"""Lease (C6 step 8, I6): lease_expiry is the only mutable field of a snapshot; the sentinel's heartbeat pushes it forward, silence lets it lapse."""

from lexi.common.clock import Clock
from lexi.common.types import RegistrySnapshot


class Lease:
    def __init__(self, clock: Clock, ttl_s: float) -> None:
        self.clock, self.ttl_s = clock, ttl_s
        self.lost = True  # no heartbeat seen yet in this process

    def extend(self, snap: RegistrySnapshot) -> bool:
        """Heartbeat. Returns True when this restores a lease that had been lost (or never held)."""
        snap.lease_expiry = self.clock.now() + self.ttl_s
        restored, self.lost = self.lost, False
        return restored

    def newly_lost(self, snap: RegistrySnapshot) -> bool:
        """True exactly once per loss, so each loss becomes one REGISTRY_EVENT."""
        if self.lost or self.clock.now() < snap.lease_expiry:
            return False
        self.lost = True
        return True
