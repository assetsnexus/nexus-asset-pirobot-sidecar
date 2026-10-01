"""Hardcoded RaspTank joint limits (PCA9685 channels 0–4).

Stock Adeept range is 0–180° electrically. On this robot mechanical stops are
tighter — especially the shoulder (ch0): mid (~90°) holds forward and stalls,
and angles toward 180° drive past the down stop. Keep channel 0 near upright.

Shoulder horn is mounted so PWM increases toward *forward/down*. The slider
exposes a logical angle with the opposite sense (higher = more upright), so
``invert=True`` maps UI ↔ PWM as ``pwm = min+max-ui``.
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
    rest_deg: int  # logical (UI) rest; for inverted joints this is upright
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
        """PCA9685 degrees → logical slider angle."""
        pwm = self.clamp(int(pwm_deg))
        if not self.invert:
            return pwm
        return self.min_deg + self.max_deg - pwm

    @property
    def rest_pwm(self) -> int:
        return self.ui_to_pwm(self.rest_deg)


# Shoulder: PWM 0° = upright, PWM↑ = forward/down. Slider is inverted so
# dragging up/right raises the arm (logical rest = max = upright).
RASPTANK_SERVO_LIMITS: Dict[int, ServoLimit] = {
    0: ServoLimit(0, "Shoulder", 0, 85, 85, invert=True),
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
