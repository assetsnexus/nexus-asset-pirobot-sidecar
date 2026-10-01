"""Hardcoded RaspTank joint limits (PCA9685 channels 0–4).

Stock Adeept range is 0–180° electrically. On this robot mechanical stops are
tighter — especially the shoulder (ch0):

* PWM **0°** = upright rest (90° opposite stock mid).
* PWM **↑** = forward / down toward the stall (stock mid ~90° jams).

Slider degrees are **PWM degrees** for the shoulder (no invert). Keep max well
below the forward stall so the arm cannot be commanded past the stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class ServoLimit:
    channel: int
    name: str
    min_deg: int
    max_deg: int
    rest_deg: int  # PWM / slider degrees (same space unless invert)
    invert: bool = False

    def clamp(self, deg: int) -> int:
        return max(self.min_deg, min(self.max_deg, int(deg)))

    def ui_to_pwm(self, ui_deg: int) -> int:
        """Logical slider angle → PCA9685 setPWM degrees."""
        ui = self.clamp(ui_deg)
        if not self.invert:
            return ui
        return self.min_deg + self.max_deg - ui

    def pwm_to_ui(self, pwm_deg: int) -> int:
        """PCA9685 degrees → logical slider angle.

        Do **not** clamp PWM into [min,max] before mapping — vendor ``nowPos``
        may still be stock mid (90°) outside our safe window; clamping first
        then inverting would flip upright/down incorrectly.
        """
        pwm = max(0, min(180, int(pwm_deg)))
        if self.invert:
            ui = self.min_deg + self.max_deg - pwm
        else:
            ui = pwm
        return self.clamp(ui)

    @property
    def rest_pwm(self) -> int:
        return self.ui_to_pwm(self.rest_deg)

    def endstop_away_from_rest(self) -> int:
        """Opposite joint limit from rest (for armDown / fold)."""
        mid = (self.min_deg + self.max_deg) / 2.0
        if self.rest_deg <= mid:
            return self.max_deg
        return self.min_deg


# Shoulder: UI == PWM. Rest/upright at 0°. Cap max below forward stall (~90°).
# 50° leaves margin before the mechanical jam that overheats the servo.
RASPTANK_SERVO_LIMITS: Dict[int, ServoLimit] = {
    0: ServoLimit(0, "Shoulder", 0, 50, 0, invert=False),
    1: ServoLimit(1, "Elbow", 15, 165, 90),
    2: ServoLimit(2, "Wrist", 20, 160, 90),
    3: ServoLimit(3, "Gripper", 40, 140, 90),
    4: ServoLimit(4, "Camera", 40, 140, 90),
}


def servo_limit(channel: int) -> ServoLimit:
    if channel not in RASPTANK_SERVO_LIMITS:
        raise KeyError(f"unknown servo channel {channel}")
    return RASPTANK_SERVO_LIMITS[channel]


def all_limits() -> Tuple[ServoLimit, ...]:
    return tuple(RASPTANK_SERVO_LIMITS[i] for i in sorted(RASPTANK_SERVO_LIMITS))
