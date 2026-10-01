"""WS2812 status lights: red blink when control socket is down, blue when connected.

Vendor Robot HAT has no sleep mode. We drive the strip via overlay blink patterns
(without editing vendor ``robotLight``). Explicit light commands (``police``,
``police_off``, breath, etc.) temporarily override; after idle while the socket
is disconnected we always return to red blink.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

MODE_RED = "red"
MODE_BLUE = "blue"


class StatusLightsController:
    """Auto red/blue blink from control-socket + idle; manual commands override."""

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
        self._last_control = self._clock()
        self._ws_connected = False
        self._manual = False
        self._applied: Optional[str] = None

    @property
    def ws_connected(self) -> bool:
        return self._ws_connected

    @property
    def manual(self) -> bool:
        return self._manual

    @property
    def applied(self) -> Optional[str]:
        return self._applied

    def set_ws_connected(self, connected: bool) -> None:
        if not self._enabled:
            return
        connected = bool(connected)
        if self._ws_connected == connected and not self._manual:
            # Still re-assert auto mode if we never applied yet.
            if self._applied is None:
                self._apply_auto(force=True)
            return
        self._ws_connected = connected
        if not self._manual:
            self._apply_auto(force=True)
        elif not connected:
            # Disconnected: drop manual override on next idle tick → red.
            pass

    def note_control(self) -> None:
        if not self._enabled:
            return
        self._last_control = self._clock()

    def note_light_override(self) -> None:
        """Any explicit light command (police / police_off / …)."""
        if not self._enabled:
            return
        self._manual = True
        self._last_control = self._clock()
        self._applied = "manual"

    def note_police_command(self, on: bool) -> None:
        """Compat for callers that still pass police on/off."""
        self.note_light_override()
        if not on:
            # police_off: resume auto after idle window from now.
            self._last_control = self._clock()

    def tick(self, now: float | None = None) -> None:
        if not self._enabled:
            return
        now = self._clock() if now is None else now
        idle = (now - self._last_control) >= self._idle_s
        if self._manual:
            if idle:
                self._manual = False
                self._apply_auto(force=True)
            return
        # Disconnected + idle must stay red even if something cleared applied.
        if not self._ws_connected and idle:
            self._apply_auto(force=self._applied != MODE_RED)
            return
        self._apply_auto(force=False)

    def _desired(self) -> str:
        return MODE_BLUE if self._ws_connected else MODE_RED

    def _apply_auto(self, *, force: bool) -> None:
        desired = self._desired()
        if not force and self._applied == desired:
            return
        try:
            if hasattr(self._hw, "set_status_blink"):
                self._hw.set_status_blink(desired)
            else:
                logger.debug("status lights: hardware has no set_status_blink")
                return
            self._applied = desired
            logger.info(
                "status lights auto %s (ws_connected=%s)",
                desired,
                self._ws_connected,
            )
        except Exception:
            logger.exception("status lights apply failed (%s)", desired)


# Backwards-compatible name used by older imports/tests.
IdlePoliceIndicator = StatusLightsController
