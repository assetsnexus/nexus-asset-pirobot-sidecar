"""Stop drive and servos when control input goes quiet."""

from __future__ import annotations

import time
from .command_router import CommandRouter


class Deadman:
    def __init__(self, router: CommandRouter, timeout_ms: int):
        self.router = router
        self.timeout_s = timeout_ms / 1000.0
        self.last_input = time.monotonic()
        self.trips = 0
        self.tripped = False

    def poke(self) -> None:
        self.last_input = time.monotonic()
        self.tripped = False

    def tick(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.tripped:
            return False
        if now - self.last_input < self.timeout_s:
            return False
        self.router.all_stop()
        self.trips += 1
        self.tripped = True
        return True
