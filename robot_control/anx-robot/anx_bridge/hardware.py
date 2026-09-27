"""Optional vendor hardware executor — imports Adeept modules without editing them.

When GPIO / HAT libraries are absent (compose dry-run, CI), actions are logged only.
Mirrors webServer_HAT_V3.1 robotCtrl / switchCtrl without patching that file.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HardwareExecutor:
    """Best-effort drive path using vendor move / switch / RPIservo."""

    def __init__(self) -> None:
        self._ready = False
        self._speed = 60
        self._direction = "no"
        self._turn = "no"
        self._move = None
        self._switch = None
        self._sc = None  # RPIservo.ServoCtrl
        self._init_error: Optional[str] = None

    def setup(self) -> bool:
        if self._ready:
            return True
        if self._init_error is not None:
            return False
        try:
            import move
            import switch
            import RPIservo

            move.setup()
            switch.switchSetup()
            sc = RPIservo.ServoCtrl()
            sc.moveInit()
            sc.start()
            self._move = move
            self._switch = switch
            self._sc = sc
            self._ready = True
            logger.info("ANX hardware executor ready (vendor move/switch/RPIservo)")
            return True
        except Exception as exc:
            self._init_error = str(exc)
            logger.warning(
                "ANX hardware executor unavailable (MQTT will log actions only): %s", exc
            )
            return False

    def __call__(self, action: str, value: Any = None) -> None:
        if not self._ready and not self.setup():
            logger.info("robot action %s value=%s (no hardware)", action, value)
            return
        move = self._move
        switch = self._switch
        sc = self._sc
        assert move is not None and switch is not None and sc is not None

        if action == "wsB":
            try:
                self._speed = max(0, min(100, int(value)))
            except (TypeError, ValueError):
                pass
            return

        if action == "forward":
            self._direction = "forward"
            move.move(self._speed, 1, "mid")
        elif action == "backward":
            self._direction = "backward"
            move.move(self._speed, -1, "no")
        elif action == "DS":
            self._direction = "no"
            if self._turn == "no":
                move.motorStop()
        elif action == "left":
            self._turn = "left"
            move.move(self._speed, 1, "left")
        elif action == "right":
            self._turn = "right"
            move.move(self._speed, 1, "right")
        elif action == "TS":
            self._turn = "no"
            if self._direction == "no":
                move.motorStop()
        elif action == "armUp":
            sc.singleServo(0, 1, 2)
        elif action == "armDown":
            sc.singleServo(0, -1, 2)
        elif action == "armStop":
            sc.stopWiggle()
        elif action == "handUp":
            sc.singleServo(1, -1, 2)
        elif action == "handDown":
            sc.singleServo(1, 1, 2)
        elif action == "handStop":
            sc.stopWiggle()
        elif action == "lookleft":
            sc.singleServo(2, 1, 2)
        elif action == "lookright":
            sc.singleServo(2, -1, 2)
        elif action == "LRstop":
            sc.stopWiggle()
        elif action == "grab":
            sc.singleServo(3, 1, 2)
        elif action == "loose":
            sc.singleServo(3, -1, 2)
        elif action == "GLstop":
            sc.stopWiggle()
        elif action == "up":
            sc.singleServo(4, -1, 1)
        elif action == "down":
            sc.singleServo(4, 1, 1)
        elif action == "UDstop":
            sc.stopWiggle()
        elif action == "home":
            for idx in range(5):
                sc.moveServoInit(idx)
        elif action == "Switch_1_on":
            switch.switch(1, 1)
        elif action == "Switch_1_off":
            switch.switch(1, 0)
        elif action == "Switch_2_on":
            switch.switch(2, 1)
        elif action == "Switch_2_off":
            switch.switch(2, 0)
        elif action == "Switch_3_on":
            switch.switch(3, 1)
        elif action == "Switch_3_off":
            switch.switch(3, 0)
        else:
            logger.debug("hardware: unhandled action %s", action)


def build_sample_fn():
    """Telemetry sample using vendor info.py when present."""

    def sample(control_source: str, deadman_trips: int) -> dict:
        from .telemetry import build_telemetry

        host: dict = {}
        try:
            import info

            host = {
                "cpu_temp_c": float(info.get_cpu_tempfunc()),
                "cpu_percent": float(info.get_cpu_use()),
                "ram_percent": float(info.get_ram_info()),
            }
        except Exception:
            host = {}
        return build_telemetry(
            control_source=control_source,
            deadman_trips=deadman_trips,
            host=host,
        )

    return sample
