"""Stop drive and servos when control input goes quiet — gated by node heartbeat / timed motion."""

from __future__ import annotations

import time
from typing import Optional

from .command_router import CommandRouter


class Deadman:
    """Failsafe stop on quiet MQTT input, with two gates:

    - If asset-node heartbeats have been seen and are still fresh, the node owns
      session timers — this deadman does not stop motion.
    - If heartbeats have never been seen (sidecar cannot observe the node), the
      legacy 500 ms quiet-input trip applies, but **not** while a timed motion
      (drive_cm / turn / sequence) is active. Timed motions refresh their own
      ``set_timed_motion_active`` flag instead of poking MQTT input.
    - When heartbeats were seen and then go stale, this failsafe stops motion.
    """

    def __init__(
        self,
        router: CommandRouter,
        timeout_ms: int,
        *,
        node_heartbeat_timeout_ms: int = 2000,
    ):
        self.router = router
        self.timeout_s = timeout_ms / 1000.0
        self.node_heartbeat_timeout_s = node_heartbeat_timeout_ms / 1000.0
        self.last_input = time.monotonic()
        self.last_node_heartbeat: Optional[float] = None
        self._had_node_heartbeat = False
        self.timed_motion_active = False
        self.trips = 0
        self.tripped = False

    def poke(self) -> None:
        self.last_input = time.monotonic()
        self.tripped = False

    def poke_node_heartbeat(self) -> None:
        """Asset-node liveness poke. While fresh, MQTT quiet deadman is suppressed."""
        self.last_node_heartbeat = time.monotonic()
        self._had_node_heartbeat = True
        self.tripped = False

    def set_timed_motion_active(self, active: bool) -> None:
        """Timed drive/sequence owns motion; do not let quiet MQTT cut it short."""
        self.timed_motion_active = bool(active)
        if active:
            self.tripped = False

    def tick(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.tripped:
            return False

        if self._had_node_heartbeat:
            assert self.last_node_heartbeat is not None
            if now - self.last_node_heartbeat < self.node_heartbeat_timeout_s:
                return False
            # Heartbeats stopped — failsafe stops motion.
            self.router.all_stop()
            self.trips += 1
            self.tripped = True
            return True

        # No node heartbeats observed: legacy quiet-MQTT deadman, gated during timed motion.
        if self.timed_motion_active:
            return False
        if now - self.last_input < self.timeout_s:
            return False
        self.router.all_stop()
        self.trips += 1
        self.tripped = True
        return True
