"""Idle indicator: blink WS2812 police lights when nobody is remotely controlling.

The Adeept Robot HAT has no sleep / power-save mode in vendor code — only battery
voltage sensing (ADS7830). This overlay uses the existing ``police()`` /
``breath()`` LED modes without editing vendor libraries.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


class _PoliceCapable(Protocol):
    def set_idle_police(self, active: bool) -> None: ...


class IdlePoliceIndicator:
    """Police blink while control is quiet; breath (or off) while actively driven.

    Explicit ``police`` / ``police_off`` commands take priority until cleared by
    the opposite command (or by ``clear_manual``).
    """

    def __init__(
        self,
        hardware: Any,
        *,
        idle_ms: int = 3000,
        enabled: bool = True,
        clock: Optional[Any] = None,
    ):
        self._hw = hardware
        self._idle_s = max(0.2, idle_ms / 1000.0)
        self._enabled = bool(enabled)
        self._clock = clock or time.monotonic
        # Start as idle so lights come on after idle_ms from boot with no control.
        self._last_control = self._clock()
        self._idle_lit = False
        self._manual: Optional[bool] = None  # True=force police, False=force off, None=auto

    @property
    def idle_lit(self) -> bool:
        return self._idle_lit

    @property
    def manual(self) -> Optional[bool]:
        return self._manual

    def note_control(self) -> None:
        """Any remote / USB / UI motion or servo command."""
        if not self._enabled:
            return
        self._last_control = self._clock()
        if self._manual is not None:
            return
        if self._idle_lit:
            self._idle_lit = False
            self._apply(False)

    def note_police_command(self, on: bool) -> None:
        """Stock ``police`` / ``police_off`` — manual override of auto idle."""
        if not self._enabled:
            return
        self._manual = bool(on)
        self._idle_lit = False
        # Hardware executor already applied the LED mode; keep state consistent.
        if not on:
            # Resume auto after police_off: restart idle timer from now.
            self._last_control = self._clock()
            self._manual = None

    def tick(self, now: float | None = None) -> None:
        if not self._enabled or self._manual is not None:
            return
        now = self._clock() if now is None else now
        quiet = (now - self._last_control) >= self._idle_s
        if quiet and not self._idle_lit:
            self._idle_lit = True
            self._apply(True)
        elif not quiet and self._idle_lit:
            self._idle_lit = False
            self._apply(False)

    def _apply(self, police_on: bool) -> None:
        try:
            if hasattr(self._hw, "set_idle_police"):
                self._hw.set_idle_police(police_on)
            elif police_on and hasattr(self._hw, "_police_on"):
                self._hw._police_on()  # noqa: SLF001
            elif not police_on and hasattr(self._hw, "_police_off"):
                self._hw._police_off()  # noqa: SLF001
            else:
                logger.debug("idle police: no hardware LED hooks")
                return
            logger.info("idle police lights %s", "on" if police_on else "off")
        except Exception:
            logger.exception("idle police apply failed (on=%s)", police_on)
