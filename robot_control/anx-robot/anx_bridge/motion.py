"""Timed open-loop motions with ultrasonic obstacle stop (overlay only)."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from .odometry import (
    DEFAULT_SPEED_AT_FULL_PWM_MPS,
    DEFAULT_TRACK_WIDTH_M,
    drive_duration_s,
    speed_mps,
    turn_90_duration_s,
)

logger = logging.getLogger(__name__)

DEFAULT_OBSTACLE_STOP_MM = 100.0
RANGE_POLL_HZ = 20.0

DEFAULT_SEQUENCE: list[dict[str, Any]] = [
    {"action": "drive_cm", "value": 100},
    {"action": "turn_right_90"},
    {"action": "drive_cm", "value": 200},
    {"action": "turn_left_90"},
]


class MotionController:
    """Runs drive_cm / 90° turns / sequences on a worker thread.

    A new motion cancels the previous one. While active, ultrasonic is polled
    at ~20 Hz; under ``obstacle_stop_mm`` motors stop and local speed is 0.
    """

    def __init__(
        self,
        *,
        drive_forward: Callable[[int], None],
        drive_backward: Callable[[int], None],
        spin_left: Callable[[int], None],
        spin_right: Callable[[int], None],
        motor_stop: Callable[[], None],
        get_pwm: Callable[[], int],
        read_ultrasonic_mm: Callable[[], Optional[float]],
        set_speed_mps: Callable[[float], None],
        accumulate_distance: Callable[[float, float], None],
        set_timed_motion_active: Optional[Callable[[bool], None]] = None,
        publish_fast: Optional[Callable[[dict[str, Any]], None]] = None,
        track_width_m: float = DEFAULT_TRACK_WIDTH_M,
        speed_at_full_pwm_mps: float = DEFAULT_SPEED_AT_FULL_PWM_MPS,
        obstacle_stop_mm: float = DEFAULT_OBSTACLE_STOP_MM,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._drive_forward = drive_forward
        self._drive_backward = drive_backward
        self._spin_left = spin_left
        self._spin_right = spin_right
        self._motor_stop = motor_stop
        self._get_pwm = get_pwm
        self._read_ultrasonic_mm = read_ultrasonic_mm
        self._set_speed_mps = set_speed_mps
        self._accumulate_distance = accumulate_distance
        self._set_timed_motion_active = set_timed_motion_active
        self._publish_fast = publish_fast
        self.track_width_m = track_width_m
        self.speed_at_full_pwm_mps = speed_at_full_pwm_mps
        self.obstacle_stop_mm = obstacle_stop_mm
        self._sleep = sleep_fn
        self._monotonic = monotonic_fn
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active = False
        self.last_obstacle_stop = False

    @property
    def active(self) -> bool:
        return self._active

    def attach(
        self,
        *,
        set_timed_motion_active: Optional[Callable[[bool], None]] = None,
        publish_fast: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        if set_timed_motion_active is not None:
            self._set_timed_motion_active = set_timed_motion_active
        if publish_fast is not None:
            self._publish_fast = publish_fast

    def cancel(self) -> None:
        """Cancel any running timed motion (does not itself stop motors)."""
        self._cancel.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=2.0)

    def stop(self) -> None:
        """Idempotent stop: cancel motion, motors off, speed 0."""
        self.cancel()
        self._finish_motion(speed=0.0)
        try:
            self._motor_stop()
        except Exception as exc:
            logger.debug("motor_stop failed: %s", exc)

    def start_turn_left_90(self) -> None:
        self._start_job(lambda: self._run_turn("left"))

    def start_turn_right_90(self) -> None:
        self._start_job(lambda: self._run_turn("right"))

    def start_drive_cm(self, cm: Any) -> None:
        try:
            distance_cm = float(cm)
        except (TypeError, ValueError):
            logger.warning("drive_cm bad value %r", cm)
            return
        self._start_job(lambda: self._run_drive_cm(distance_cm))

    def start_sequence(self, steps: Optional[list] = None) -> None:
        seq = list(steps) if steps else list(DEFAULT_SEQUENCE)
        self._start_job(lambda: self._run_sequence(seq))

    def _start_job(self, job: Callable[[], None]) -> None:
        self.cancel()
        self._cancel.clear()
        self.last_obstacle_stop = False

        def runner() -> None:
            self._set_active(True)
            try:
                job()
            finally:
                self._set_active(False)
                self._thread = None

        self._thread = threading.Thread(target=runner, name="anx-motion", daemon=True)
        self._thread.start()

    def _set_active(self, active: bool) -> None:
        self._active = active
        if self._set_timed_motion_active is not None:
            try:
                self._set_timed_motion_active(active)
            except Exception:
                pass

    def _finish_motion(self, speed: float) -> None:
        self._set_speed_mps(speed)
        if speed == 0.0:
            try:
                self._motor_stop()
            except Exception:
                pass

    def _current_v(self) -> tuple[int, float]:
        pwm = max(0, min(100, int(self._get_pwm())))
        v = speed_mps(pwm, self.speed_at_full_pwm_mps)
        return pwm, v

    def _poll_range_and_maybe_stop(self) -> bool:
        """Return True if obstacle stop fired."""
        mm = self._read_ultrasonic_mm()
        if self._publish_fast is not None:
            try:
                self._publish_fast(
                    {
                        "ultrasonic_mm": mm,
                        "distance_mm": mm,
                    }
                )
            except Exception:
                pass
        if mm is not None and mm < self.obstacle_stop_mm:
            self.last_obstacle_stop = True
            self._finish_motion(0.0)
            logger.info("obstacle stop at %.1f mm (threshold %.1f)", mm, self.obstacle_stop_mm)
            return True
        return False

    def _run_for_duration(self, duration_s: float, *, signed_speed: float) -> bool:
        """Drive until duration or cancel/obstacle. Returns False if aborted."""
        if duration_s <= 0 or duration_s == float("inf"):
            self._finish_motion(0.0)
            return False
        interval = 1.0 / RANGE_POLL_HZ
        start = self._monotonic()
        last = start
        self._set_speed_mps(abs(signed_speed))
        while not self._cancel.is_set():
            now = self._monotonic()
            elapsed = now - start
            remaining = duration_s - elapsed
            # Sub-microsecond remainders are float noise; avoid spin-sleep forever.
            if remaining <= 1e-6:
                break
            dt = now - last
            last = now
            if dt > 0:
                self._accumulate_distance(abs(signed_speed), dt)
            if self._poll_range_and_maybe_stop():
                return False
            self._sleep(min(interval, remaining))
        if self._cancel.is_set():
            self._finish_motion(0.0)
            return False
        self._finish_motion(0.0)
        return True

    def _run_turn(self, direction: str) -> None:
        pwm, v = self._current_v()
        duration = turn_90_duration_s(self.track_width_m, v)
        if direction == "left":
            self._spin_left(pwm)
        else:
            self._spin_right(pwm)
        # Spin does not advance open-loop path distance along heading; still track wheel travel.
        self._run_for_duration(duration, signed_speed=v)

    def _run_drive_cm(self, distance_cm: float) -> None:
        pwm, v = self._current_v()
        distance_m = distance_cm / 100.0
        duration = drive_duration_s(distance_m, v)
        if distance_m >= 0:
            self._drive_forward(pwm)
            signed = v
        else:
            self._drive_backward(pwm)
            signed = v
        self._run_for_duration(duration, signed_speed=signed)

    def _run_sequence(self, steps: list) -> None:
        for step in steps:
            if self._cancel.is_set() or self.last_obstacle_stop:
                break
            if not isinstance(step, dict):
                continue
            action = step.get("action")
            value = step.get("value")
            if action == "drive_cm":
                self._run_drive_cm(float(value) if value is not None else 0.0)
            elif action == "turn_left_90":
                self._run_turn("left")
            elif action == "turn_right_90":
                self._run_turn("right")
            elif action in ("stop", "DS"):
                self._finish_motion(0.0)
            else:
                logger.warning("sequence skips unknown step %s", action)
            if self.last_obstacle_stop:
                break
