"""Startup shoulder/servo choreography: wait → blink → default pose → limp.

Vendor ``moveInit`` must not hold torque against a stop. After the control
stack is up we wait briefly (default 3 s) so a reconnecting UI can claim the
socket, then blink status LEDs, drive joints to the overlay rest pose, and
release PWM (idle/limp).

If a control WebSocket connects during the wait or move, the sequence aborts
and leaves connected-mode behavior alone (no forced park/limp fight).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_cancel = threading.Event()
_generation = 0


def boot_init_enabled() -> bool:
    raw = os.environ.get("ANX_BOOT_INIT", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def boot_init_delay_s() -> float:
    raw = os.environ.get("ANX_BOOT_INIT_DELAY_S", "3").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("ANX_BOOT_INIT_DELAY_S=%r invalid; using 3", raw)
        return 3.0


def boot_init_settle_s() -> float:
    raw = os.environ.get("ANX_BOOT_INIT_SETTLE_S", "1.2").strip()
    try:
        return max(0.1, float(raw))
    except ValueError:
        logger.warning("ANX_BOOT_INIT_SETTLE_S=%r invalid; using 1.2", raw)
        return 1.2


def _control_connected() -> bool:
    try:
        from . import bridge

        return bool(bridge.control_socket_connected())
    except Exception:
        return False


def _blink_boot() -> None:
    try:
        from . import bridge

        lights = getattr(bridge, "_status_lights", None)
        if lights is not None and hasattr(lights, "set_ws_connected"):
            # Re-assert disconnected blink (red) for the boot cue.
            if not _control_connected():
                lights.set_ws_connected(False)
        hw = getattr(getattr(bridge, "_executor_ref", None), "set_status_blink", None)
        if callable(hw) and not _control_connected():
            hw("red")
    except Exception:
        logger.debug("boot blink failed", exc_info=True)


def _move_to_default() -> None:
    from .servo_positions import apply_rest_pose
    from .servos import ensure_servos_armed, stop_all_wiggle

    stop_all_wiggle()
    ensure_servos_armed(park_arm=False)
    apply_rest_pose()


def _go_idle() -> None:
    from .servos import release_servos, stop_all_wiggle

    stop_all_wiggle()
    release_servos()


def _sleep_interruptible(seconds: float, *, gen: int, connected: Callable[[], bool]) -> bool:
    """Sleep up to ``seconds``. Return True if aborted (cancel or connected)."""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if _cancel.is_set():
            return True
        with _lock:
            if gen != _generation:
                return True
        if connected():
            return True
        time.sleep(min(0.1, deadline - time.monotonic()))
    return _cancel.is_set() or connected()


def _run(gen: int, delay_s: float, settle_s: float) -> None:
    connected = _control_connected
    logger.info(
        "servo boot init scheduled (delay=%.1fs settle=%.1fs)",
        delay_s,
        settle_s,
    )
    if _sleep_interruptible(delay_s, gen=gen, connected=connected):
        logger.info("servo boot init interrupted during wait (connected or cancelled)")
        return

    if connected():
        logger.info("servo boot init skipped — control socket already connected")
        return

    logger.info("servo boot init: blink")
    _blink_boot()
    if _sleep_interruptible(0.35, gen=gen, connected=connected):
        logger.info("servo boot init interrupted after blink")
        return

    logger.info("servo boot init: move to default rest pose")
    try:
        _move_to_default()
    except Exception:
        logger.exception("servo boot init: move to default failed")
        try:
            _go_idle()
        except Exception:
            logger.exception("servo boot init: idle after move failure failed")
        return

    if _sleep_interruptible(settle_s, gen=gen, connected=connected):
        logger.info(
            "servo boot init interrupted during settle — limp then leave connected state"
        )
        try:
            _go_idle()
        except Exception:
            logger.exception("servo boot init: limp after interrupt failed")
        return

    logger.info("servo boot init: release PWM (idle/limp)")
    try:
        _go_idle()
    except Exception:
        logger.exception("servo boot init: release failed")
        return
    logger.info("servo boot init complete")


def cancel_boot_init() -> None:
    """Abort a scheduled/running boot sequence (e.g. control socket connected)."""
    global _generation
    with _lock:
        _generation += 1
        _cancel.set()
    logger.info("servo boot init cancel requested")


def schedule_boot_init(
    *,
    delay_s: float | None = None,
    settle_s: float | None = None,
) -> bool:
    """Start (or restart) the boot choreography on a daemon thread."""
    global _thread, _generation
    if not boot_init_enabled():
        logger.info("servo boot init disabled (ANX_BOOT_INIT)")
        return False
    delay = boot_init_delay_s() if delay_s is None else max(0.0, float(delay_s))
    settle = boot_init_settle_s() if settle_s is None else max(0.1, float(settle_s))
    with _lock:
        _generation += 1
        gen = _generation
        _cancel.clear()
        t = threading.Thread(
            target=_run,
            args=(gen, delay, settle),
            name="anx-servo-boot-init",
            daemon=True,
        )
        _thread = t
        t.start()
    return True
