"""Servo idle gate for the RaspTank overlay (vendor RPIservo is not edited).

Stock ``moveInit()`` drives every channel to mid (90°). Holding torque against
a mechanical stop overheats servos. Policy:

1. Boot / disconnect / process exit → **PWM off** (limp), never hold a rest pose.
2. Connect does **not** park — first motion/slider command enables PWM.
3. ``home`` releases PWM instead of driving init angles into the stops.
"""
from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)

ARM_CHANNEL = 0
SERVO_CHANNELS = 8
# Stock mid (90°) stalls the shoulder forward. Init must aim 90° the other way
# (0°) — never drive/hold 90° or 180° on channel 0.
_DEFAULT_ARM_REST_DEG = 0

_armed = False
_primary_ctrl: Any = None
_all_ctrls: List[Any] = []
_hooks_installed = False
_move_init_patched = False
_release_lock = threading.Lock()


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
    """Remember live ServoCtrl instance(s) that share the PCA9685."""
    global _primary_ctrl
    if servo_ctrl is None:
        return
    _primary_ctrl = servo_ctrl
    if servo_ctrl not in _all_ctrls:
        _all_ctrls.append(servo_ctrl)


def _release_one(ctrl: Any, *, channels: int) -> int:
    pwm = getattr(ctrl, "pwm_servo", None)
    if pwm is None:
        return 0
    released = 0
    for i in range(channels):
        try:
            pwm.channels[i].duty_cycle = 0
            released += 1
        except Exception as exc:
            logger.debug("release channel %s failed: %s", i, exc)
    # Soft-sleep the chip when possible so carriers drop completely.
    try:
        if hasattr(pwm, "deinit"):
            # Keep the object usable: prefer MODE1 sleep over full deinit.
            pass
        # adafruit_pca9685 exposes .channels; some builds have .chip
        chip = getattr(pwm, "_pca", None) or getattr(pwm, "pca", None) or pwm
        if hasattr(chip, "mode1"):
            # not always writable the same way across versions
            pass
    except Exception:
        logger.debug("pca soft-sleep skipped", exc_info=True)
    if hasattr(ctrl, "pause"):
        try:
            flag = getattr(ctrl, "_ServoCtrl__flag", None)
            if flag is not None:
                flag.clear()
            else:
                ctrl.pause()
        except Exception:
            pass
    return released


def release_servos(servo_ctrl: Any = None, *, channels: int = SERVO_CHANNELS) -> None:
    """Drop PWM on servo channels so motors go limp (no holding torque)."""
    global _armed
    with _release_lock:
        targets: List[Any]
        if servo_ctrl is not None:
            targets = [servo_ctrl]
            register_servo_ctrl(servo_ctrl)
        else:
            targets = [c for c in _all_ctrls if c is not None]
            if not targets and _primary_ctrl is not None:
                targets = [_primary_ctrl]
        if not targets:
            logger.debug("release_servos: no pwm_servo ctrl registered")
            return
        total = 0
        for ctrl in targets:
            total += _release_one(ctrl, channels=channels)
        _armed = False
        logger.info(
            "servos released (PWM off, %s channel-ops across %s ctrl(s)) — limp",
            total,
            len(targets),
        )


def park_arm_upright(servo_ctrl: Any, deg: int | None = None) -> None:
    """Explicit shoulder park only (sliders/home callers). Avoid on connect/boot."""
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
            "parked arm servo (ch%d) at %s° (was %s → now %s)",
            ARM_CHANNEL,
            target,
            before,
            after,
        )
    except Exception:
        logger.exception("failed to park arm servo at %s°", target)


def ensure_servos_armed(servo_ctrl: Any = None, *, park_arm: bool = False) -> None:
    """Mark servos usable. Does not hold a rest pose unless park_arm=True."""
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
    logger.info("servos armed for control (park_arm=%s)", park_arm)


def release_servos_if_idle(*, connected: bool) -> None:
    """When the UI socket is down, drop holding torque again."""
    if connected:
        return
    if not _armed:
        # Still force PWM off — vendor may have left channels hot.
        release_servos(_primary_ctrl)
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
    release_servos()


def install_shutdown_release_hooks() -> None:
    """Ensure docker stop / SIGTERM / process exit drops PWM (no hot hold)."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True

    def _hook(*_args) -> None:
        try:
            release_servos()
        except Exception:
            logger.exception("shutdown servo release failed")

    atexit.register(_hook)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            prev = signal.getsignal(sig)

            def _handler(signum, frame, _prev=prev, _sig=sig):
                _hook()
                if callable(_prev) and _prev not in (signal.SIG_DFL, signal.SIG_IGN):
                    _prev(signum, frame)
                elif _prev == signal.SIG_DFL:
                    signal.signal(signum, signal.SIG_DFL)
                    os.kill(os.getpid(), signum)

            signal.signal(sig, _handler)
        except Exception:
            logger.debug("could not install %s release hook", sig, exc_info=True)
    logger.info("servo release hooks installed (atexit + SIGTERM/SIGINT)")


def install_vendor_move_init_patch() -> None:
    """Patch vendor ``ServoCtrl.moveInit`` before webServer imports it.

    Stock ``moveInit()`` drives every channel to mid (90°). On this tank that
    jams the shoulder forward. We force shoulder init to ``ANX_ARM_REST_DEG``
    (default 0° = 90° opposite from stock mid) then immediately drop PWM so
    the pose is not held against a stop.
    """
    global _move_init_patched
    if _move_init_patched:
        return
    try:
        import RPIservo
    except Exception as exc:
        logger.warning("cannot patch RPIservo.moveInit (%s)", exc)
        return

    rest = arm_rest_deg()
    try:
        # Module-level defaults used when ServoCtrl builds initPos.
        if hasattr(RPIservo, "init_pwm0"):
            RPIservo.init_pwm0 = rest
    except Exception:
        logger.debug("could not set RPIservo.init_pwm0", exc_info=True)

    if not hasattr(RPIservo, "ServoCtrl"):
        return
    orig = RPIservo.ServoCtrl.moveInit

    def move_init_safe(self, *args, **kwargs):
        try:
            if hasattr(self, "initPos") and len(self.initPos) > ARM_CHANNEL:
                self.initPos[ARM_CHANNEL] = arm_rest_deg()
        except Exception:
            logger.debug("initPos rewrite failed", exc_info=True)
        try:
            orig(self, *args, **kwargs)
        finally:
            register_servo_ctrl(self)
            # Never leave holding torque after vendor init.
            release_servos(self)
            logger.info(
                "moveInit patched: shoulder initPos=%s° then PWM released",
                arm_rest_deg(),
            )

    RPIservo.ServoCtrl.moveInit = move_init_safe  # type: ignore[method-assign]
    _move_init_patched = True
    logger.info(
        "patched RPIservo.ServoCtrl.moveInit (shoulder rest=%s°, then limp)",
        rest,
    )
