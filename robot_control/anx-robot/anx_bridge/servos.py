"""Servo rest poses for the RaspTank overlay (vendor RPIservo is not edited).

Channel 0 is the shoulder ("arm" / servo A). Stock ``moveInit()`` uses
``init_pwm0`` (default 90). Vendor ``webServer.py`` then constructs more
``ServoCtrl`` instances; the shoulder can be left holding the arm forward
(parallel to ground), which stalls the servo and overheats it.

Park channel 0 at the upright rest angle on start (default 90°).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

ARM_CHANNEL = 0


def arm_rest_deg() -> int:
    raw = os.environ.get("ANX_ARM_REST_DEG", "90").strip()
    try:
        deg = int(raw)
    except ValueError:
        logger.warning("ANX_ARM_REST_DEG=%r invalid; using 90", raw)
        return 90
    return max(0, min(180, deg))


def park_arm_upright(servo_ctrl: Any, deg: int | None = None) -> None:
    """Move shoulder (channel 0) to the upright rest angle and remember it as init."""
    if servo_ctrl is None:
        return
    target = arm_rest_deg() if deg is None else max(0, min(180, int(deg)))
    try:
        if hasattr(servo_ctrl, "initConfig"):
            servo_ctrl.initConfig(ARM_CHANNEL, target, 1)
        elif hasattr(servo_ctrl, "setPWM"):
            servo_ctrl.setPWM(ARM_CHANNEL, target)
        else:
            logger.warning("servo ctrl has no initConfig/setPWM; cannot park arm")
            return
        logger.info("parked arm servo (ch%d) at %s° (upright rest)", ARM_CHANNEL, target)
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
