"""Single command path for WebSocket, MQTT, and USB controller input."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DRIVE = {"forward", "backward", "left", "right", "DS", "TS"}
ARM = {
    "armUp", "armDown", "armStop",
    "handUp", "handDown", "handStop",
    "lookleft", "lookright", "LRstop",
    "grab", "loose", "GLstop",
}
CAMERA = {"up", "down", "UDstop", "home"}
MODES = {
    "automatic", "automaticOff",
    "trackLine", "trackLineOff",
    "steadyCamera", "steadyCameraOff",
    "CVFL", "CVFLColorSet", "CVFLL1", "CVFLL2", "CVFLSP",
}
SWITCHES = {
    "Switch_1_on", "Switch_1_off",
    "Switch_2_on", "Switch_2_off",
    "Switch_3_on", "Switch_3_off",
}
MOTION = {
    "turn_left_90",
    "turn_right_90",
    "drive_cm",
    "drive_sequence",
    "stop",
    "police",
    "police_off",
}
KNOWN = DRIVE | ARM | CAMERA | MODES | SWITCHES | MOTION | {"wsB", "allStop"}


class CommandRouter:
    def __init__(self, executor: Optional[Callable[..., None]] = None):
        self._executor = executor
        self.last_action: Optional[str] = None
        self.last_value: Any = None
        self.last_steps: Any = None
        self.calls: list[tuple[str, Any]] = []

    def execute(self, action: str, value: Any = None, steps: Any = None) -> bool:
        if not action or action not in KNOWN:
            logger.warning("unknown robot action %s", action)
            return False
        if action in ("stop", "allStop"):
            self.all_stop()
            return True
        self.last_action = action
        self.last_value = value
        self.last_steps = steps
        self.calls.append((action, value))
        if self._executor is not None:
            self._invoke_executor(action, value, steps)
        else:
            logger.info("robot action %s value=%s (no hardware executor)", action, value)
        return True

    def _invoke_executor(self, action: str, value: Any, steps: Any) -> None:
        assert self._executor is not None
        try:
            self._executor(action, value, steps)
        except TypeError:
            self._executor(action, value)

    def all_stop(self) -> None:
        """Idempotent full stop (drive + arm stops + motion cancel via executor)."""
        self.last_action = "stop"
        self.last_value = None
        self.last_steps = None
        self.calls.append(("stop", None))
        if self._executor is not None:
            self._invoke_executor("stop", None, None)
        self.calls.append(("DS", None))
        self.calls.append(("TS", None))
        if self._executor is not None:
            # Ensure legacy DS/TS still fire for non-motion executors that ignore "stop".
            self._invoke_executor("DS", None, None)
            self._invoke_executor("TS", None, None)
            for stop in ("armStop", "handStop", "LRstop", "GLstop", "UDstop"):
                self.calls.append((stop, None))
                self._invoke_executor(stop, None, None)
        else:
            for stop in ("armStop", "handStop", "LRstop", "GLstop", "UDstop"):
                self.calls.append((stop, None))
