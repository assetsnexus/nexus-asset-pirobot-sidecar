"""Servo rest poses for the RaspTank overlay (vendor RPIservo is not edited).

Channel 0 is the shoulder ("arm" / servo A). Stock ``moveInit()`` homes every
servo to ``init_pwm*`` which defaults to **90°**. On this tank that mid pose
holds the arm **parallel to the ground**, so the servo stalls and overheats.

Park channel 0 at an upright rest angle on start. Default is **0°** (folded up).
Vendor ``initConfig`` rejects 0 and 180 (exclusive bounds); we drive via
``setPWM`` / ``set_angle`` and still update ``initPos`` so later ``home`` /
``moveAngle`` stay consistent.

Override with ``ANX_ARM_REST_DEG`` (0–180) if the horn is mounted the other way.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ARM_CHANNEL = 0
# Stock mid (90) is the forward stall pose on RaspTank; upright rest is near 0°.
_DEFAULT_ARM_REST_DEG = 0


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


def park_arm_upright(servo_ctrl: Any, deg: int | None = None) -> None:
    """Move shoulder (channel 0) to the upright rest angle and remember it as init."""
    if servo_ctrl is None:
        return
    target = arm_rest_deg() if deg is None else max(0, min(180, int(deg)))
    try:
        before = None
        if hasattr(servo_ctrl, "nowPos") and len(servo_ctrl.nowPos) > ARM_CHANNEL:
            before = servo_ctrl.nowPos[ARM_CHANNEL]
        # Prefer setPWM: vendor initConfig uses exclusive (0,180) and skips endpoints.
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


def park_arm_on_controllers(controllers: Iterable[Any], deg: int | None = None) -> None:
    """Apply upright rest to every live ServoCtrl (PCA9685 is shared at 0x5f)."""
    seen: set[int] = set()
    for ctrl in controllers:
        if ctrl is None:
            continue
        ident = id(ctrl)
        if ident in seen:
            continue
        seen.add(ident)
        park_arm_upright(ctrl, deg=deg)
