"""Absolute servo-angle set/get for UI sliders (overlay; vendor tree untouched)."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from .servo_limits import ServoLimit, all_limits, servo_limit
from .servos import ensure_servos_armed, register_servo_ctrl

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ctrl: Any = None
_angles: Dict[int, int] = {}


def bind_servo_ctrl(servo_ctrl: Any) -> None:
    """Remember the live ServoCtrl that owns the PCA9685."""
    global _ctrl
    if servo_ctrl is None:
        return
    with _lock:
        _ctrl = servo_ctrl
        register_servo_ctrl(servo_ctrl)
        # Seed remembered *logical* angles from vendor nowPos / initPos (PWM).
        for lim in all_limits():
            if lim.channel in _angles:
                continue
            seeded_pwm = None
            for attr in ("nowPos", "initPos"):
                arr = getattr(servo_ctrl, attr, None)
                if arr is not None and len(arr) > lim.channel:
                    seeded_pwm = int(arr[lim.channel])
                    break
            if seeded_pwm is None:
                _angles[lim.channel] = lim.rest_deg
            else:
                _angles[lim.channel] = lim.pwm_to_ui(seeded_pwm)


def current_angles() -> Dict[int, int]:
    with _lock:
        out: Dict[int, int] = {}
        for lim in all_limits():
            out[lim.channel] = int(_angles.get(lim.channel, lim.rest_deg))
        return out


def describe_servos() -> List[dict]:
    angles = current_angles()
    return [
        {
            "channel": lim.channel,
            "name": lim.name,
            "deg": angles[lim.channel],
            "min": lim.min_deg,
            "max": lim.max_deg,
            "rest": lim.rest_deg,
            "invert": lim.invert,
        }
        for lim in all_limits()
    ]


def set_servo_angle(channel: int, deg: int, *, ctrl: Any = None) -> int:
    """Drive one channel to a logical (slider) angle; clamped + optional invert."""
    lim = servo_limit(channel)
    ui = lim.clamp(deg)
    pwm = lim.ui_to_pwm(ui)
    with _lock:
        sc = ctrl if ctrl is not None else _ctrl
        if sc is None:
            raise RuntimeError("no servo controller bound")
        bind_servo_ctrl(sc)
        ensure_servos_armed(sc, park_arm=False)
        if hasattr(sc, "setPWM"):
            sc.setPWM(channel, pwm)
        elif hasattr(sc, "set_angle"):
            sc.set_angle(channel, pwm)
        else:
            raise RuntimeError("servo ctrl cannot set angle")
        _angles[channel] = ui
        logger.info(
            "servo ch%s (%s) UI %s° → PWM %s° (limits %s–%s%s)",
            channel,
            lim.name,
            ui,
            pwm,
            lim.min_deg,
            lim.max_deg,
            ", inverted" if lim.invert else "",
        )
        return ui


def set_many(positions: Dict[int, int], *, ctrl: Any = None) -> Dict[int, int]:
    applied: Dict[int, int] = {}
    for ch, deg in positions.items():
        applied[int(ch)] = set_servo_angle(int(ch), int(deg), ctrl=ctrl)
    return applied


def apply_rest_pose(*, ctrl: Any = None) -> Dict[int, int]:
    return set_many({lim.channel: lim.rest_deg for lim in all_limits()}, ctrl=ctrl)


def parse_ws_servo_set(command: str) -> Optional[tuple[int, int]]:
    """Parse ``servoSet:<channel>:<deg>`` from the control WebSocket."""
    if not isinstance(command, str) or not command.startswith("servoSet:"):
        return None
    parts = command.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None
