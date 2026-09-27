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
KNOWN = DRIVE | ARM | CAMERA | MODES | SWITCHES | {"wsB"}


class CommandRouter:
    def __init__(self, executor: Optional[Callable[[str, Any], None]] = None):
        self._executor = executor
        self.last_action: Optional[str] = None
        self.last_value: Any = None
        self.calls: list[tuple[str, Any]] = []

    def execute(self, action: str, value: Any = None) -> bool:
        if not action or action not in KNOWN:
            logger.warning("unknown robot action %s", action)
            return False
        self.last_action = action
        self.last_value = value
        self.calls.append((action, value))
        if self._executor is not None:
            self._executor(action, value)
        else:
            logger.info("robot action %s value=%s (no hardware executor)", action, value)
        return True

    def all_stop(self) -> None:
        self.execute("DS")
        self.execute("TS")
        for stop in ("armStop", "handStop", "LRstop", "GLstop", "UDstop"):
            self.execute(stop)
