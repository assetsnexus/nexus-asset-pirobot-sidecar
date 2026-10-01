"""Hardcoded RaspTank joint limits (PCA9685 channels 0–4).

Stock Adeept range is 0–180° electrically. On this robot mechanical stops are
tighter — especially the shoulder (ch0): mid (~90°) holds forward and stalls,
and angles toward 180° drive past the down stop. Keep channel 0 near upright.
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
    rest_deg: int

    def clamp(self, deg: int) -> int:
        return max(self.min_deg, min(self.max_deg, int(deg)))


# Shoulder: stock mid 90° stalls forward; init aims 0° (90° the other way).
# Cap max below forward stall; allow 0° upright rest.
RASPTANK_SERVO_LIMITS: Dict[int, ServoLimit] = {
    0: ServoLimit(0, "Shoulder", 0, 85, 0),
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
