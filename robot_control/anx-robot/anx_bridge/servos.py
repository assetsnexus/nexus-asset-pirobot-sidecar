"""Servo idle gate for the RaspTank overlay (vendor RPIservo is not edited).

Stock ``moveInit()`` drives every channel to mid (90°). On this tank that holds
the shoulder forward and overheats the servo. We:

1. **Release** PCA9685 PWM (duty_cycle=0) after boot so servos stay limp until
   a control socket connects or a motion command arrives.
2. **Arm** on first connect/control: park shoulder at ``ANX_ARM_REST_DEG``
   (default 0° upright on this horn mount), then leave normal control alone.
3. **Release again** when the UI socket disconnects and control goes idle.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

ARM_CHANNEL = 0
SERVO_CHANNELS = 8
# Stock mid (90) holds the arm forward (stall). On this tank 180° drives past
# the down stop; upright rest is the other extreme (0°).
_DEFAULT_ARM_REST_DEG = 0

_armed = False
_primary_ctrl: Any = None


def arm_rest_deg() -> int:
    raw = os.environ.get("ANX_ARM_REST_DEG", str(_DEFAULT_ARM_REST_DEG)).strip()
    try:
        deg = int(raw)
    except ValueError:
        logger.warning(
            "ANX_ARM_REST_DEG=%r invalid; using %s", raw, _DEFAULT_ARM_REST_DEG
        )
        return _DEFAULT_ARM_REST_DEG
    return max(0, min(180, deg))


def servos_armed() -> bool:
    return _armed


def register_servo_ctrl(servo_ctrl: Any) -> None:
    """Remember one live ServoCtrl that owns the shared PCA9685."""
    global _primary_ctrl
    if servo_ctrl is not None:
        _primary_ctrl = servo_ctrl


def release_servos(servo_ctrl: Any = None, *, channels: int = SERVO_CHANNELS) -> None:
    """Drop PWM on servo channels so motors go limp (no holding torque)."""
    global _armed
    ctrl = servo_ctrl if servo_ctrl is not None else _primary_ctrl
    if ctrl is None:
        return
    pwm = getattr(ctrl, "pwm_servo", None)
    if pwm is None:
        logger.debug("release_servos: no pwm_servo on ctrl")
        return
    released = 0
    for i in range(channels):
        try:
            pwm.channels[i].duty_cycle = 0
            released += 1
        except Exception as exc:
            logger.debug("release channel %s failed: %s", i, exc)
    _armed = False
    if hasattr(ctrl, "pause"):
        try:
            # Avoid vendor print spam when quieted; still clear the move flag.
            flag = getattr(ctrl, "_ServoCtrl__flag", None)
            if flag is not None:
                flag.clear()
            else:
                ctrl.pause()
        except Exception:
            pass
    logger.info("servos released (PWM off on %s channels) — idle until control", released)


def park_arm_upright(servo_ctrl: Any, deg: int | None = None) -> None:
    """Move shoulder (channel 0) to the upright rest angle and remember it as init."""
    if servo_ctrl is None:
        return
    target = arm_rest_deg() if deg is None else max(0, min(180, int(deg)))
    try:
        before = None
        if hasattr(servo_ctrl, "nowPos") and len(servo_ctrl.nowPos) > ARM_CHANNEL:
            before = servo_ctrl.nowPos[ARM_CHANNEL]
        if hasattr(servo_ctrl, "initPos") and len(servo_ctrl.initPos) > ARM_CHANNEL:
            servo_ctrl.initPos[ARM_CHANNEL] = target
        if hasattr(servo_ctrl, "setPWM"):
            servo_ctrl.setPWM(ARM_CHANNEL, target)
        elif hasattr(servo_ctrl, "set_angle"):
            servo_ctrl.set_angle(ARM_CHANNEL, target)
        else:
            logger.warning("servo ctrl has no setPWM/set_angle; cannot park arm")
            return
        after = None
        if hasattr(servo_ctrl, "nowPos") and len(servo_ctrl.nowPos) > ARM_CHANNEL:
            after = servo_ctrl.nowPos[ARM_CHANNEL]
        logger.info(
            "parked arm servo (ch%d) upright rest %s° (was %s → now %s)",
            ARM_CHANNEL,
            target,
            before,
            after,
        )
    except Exception:
        logger.exception("failed to park arm servo at %s°", target)


def ensure_servos_armed(servo_ctrl: Any = None, *, park_arm: bool = True) -> None:
    """Enable servos for control: park shoulder upright once, then leave PWM on."""
    global _armed
    ctrl = servo_ctrl if servo_ctrl is not None else _primary_ctrl
    if ctrl is None:
        return
    register_servo_ctrl(ctrl)
    if _armed:
        return
    if park_arm:
        park_arm_upright(ctrl, deg=arm_rest_deg())
    _armed = True
    logger.info("servos armed for control")


def release_servos_if_idle(*, connected: bool) -> None:
    """When the UI socket is down, drop holding torque again."""
    if connected:
        return
    if not _armed:
        return
    release_servos(_primary_ctrl)


def park_arm_on_controllers(controllers: Iterable[Any], deg: int | None = None) -> None:
    """Legacy helper — prefer ensure_servos_armed / release_servos."""
    for ctrl in controllers:
        if ctrl is None:
            continue
        register_servo_ctrl(ctrl)
        park_arm_upright(ctrl, deg=deg)
        return


def release_on_controllers(controllers: Iterable[Any]) -> None:
    for ctrl in controllers:
        if ctrl is None:
            continue
        register_servo_ctrl(ctrl)
        release_servos(ctrl)
        return
