"""Open-loop PWM odometry helpers (overlay constants; not vendor)."""

from __future__ import annotations

import math


DEFAULT_WHEEL_DIAMETER_M = 0.045
DEFAULT_TRACK_WIDTH_M = 0.12
DEFAULT_SPEED_AT_FULL_PWM_MPS = 0.35


def wheel_circumference_m(wheel_diameter_m: float = DEFAULT_WHEEL_DIAMETER_M) -> float:
    return math.pi * wheel_diameter_m


def side_speeds_mps(
    pwm: float,
    direction: str,
    turn: str,
    speed_at_full_pwm_mps: float = DEFAULT_SPEED_AT_FULL_PWM_MPS,
) -> tuple[float, float]:
    """Signed ground speed for the left and right sides (m/s).

    Straight drive matches both sides. In-place spin (turn left/right) reverses
    one side, same as vendor move(). More axles can reuse this pair pattern.
    """
    v = speed_mps(pwm, speed_at_full_pwm_mps)
    if turn == "left":
        return -v, v
    if turn == "right":
        return v, -v
    if direction == "backward":
        return -v, -v
    if direction == "forward":
        return v, v
    return 0.0, 0.0


def speed_mps(pwm: float, speed_at_full_pwm_mps: float = DEFAULT_SPEED_AT_FULL_PWM_MPS) -> float:
    """Ground speed from open-loop PWM (0–100)."""
    try:
        pwm_f = float(pwm)
    except (TypeError, ValueError):
        pwm_f = 0.0
    return (pwm_f / 100.0) * float(speed_at_full_pwm_mps)


def turn_90_duration_s(
    track_width_m: float = DEFAULT_TRACK_WIDTH_M,
    v_mps: float = DEFAULT_SPEED_AT_FULL_PWM_MPS,
) -> float:
    """Time for a 90° in-place spin: t = (π/2) * (trackWidth/2) / v."""
    v = float(v_mps)
    if v <= 0:
        return float("inf")
    return (math.pi / 2.0) * (float(track_width_m) / 2.0) / v


def drive_duration_s(distance_m: float, v_mps: float) -> float:
    v = float(v_mps)
    if v <= 0:
        return float("inf")
    return abs(float(distance_m)) / v
