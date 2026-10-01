"""Per-channel servo slew: cap |ω| and |α| so absolute streams and direction flips stay smooth.

Control streams set a **goal** angle; a background tick drives PWM toward it at
``ANX_SERVO_MAX_DEG_S`` (deg/s) with optional ``ANX_SERVO_MAX_DEG_S2`` (deg/s²).
Reversing the goal (or armUp↔armDown) does not teleport — velocity ramps through zero.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_MAX_DEG_S = 90.0
_DEFAULT_MAX_DEG_S2 = 360.0  # ~0.25s to reach vmax
_TICK_HZ = 40.0


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r invalid; using %s", name, raw, default)
        return default


def max_deg_s() -> float:
    return max(1.0, _env_float("ANX_SERVO_MAX_DEG_S", _DEFAULT_MAX_DEG_S))


def max_deg_s2() -> float:
    """Acceleration limit; ``0`` disables (instant velocity toward goal)."""
    return max(0.0, _env_float("ANX_SERVO_MAX_DEG_S2", _DEFAULT_MAX_DEG_S2))


def slew_enabled() -> bool:
    raw = os.environ.get("ANX_SERVO_SLEW", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


ApplyFn = Callable[[int, float], None]


@dataclass
class _ChannelState:
    pos: float
    goal: float
    vel: float = 0.0


class ServoSlewController:
    """Thread-safe PWM-space slewer (one instance for the arm)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._channels: Dict[int, _ChannelState] = {}
        self._apply: Optional[ApplyFn] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_tick = time.monotonic()

    def set_apply(self, fn: Optional[ApplyFn]) -> None:
        with self._lock:
            self._apply = fn

    def ensure_channel(self, channel: int, pwm: float) -> None:
        with self._lock:
            if channel not in self._channels:
                p = float(pwm)
                self._channels[channel] = _ChannelState(pos=p, goal=p, vel=0.0)

    def sync_pos(self, channel: int, pwm: float) -> None:
        """Hard-snap state (after limp release / bind); clears velocity."""
        with self._lock:
            p = float(pwm)
            self._channels[channel] = _ChannelState(pos=p, goal=p, vel=0.0)

    def set_goal(self, channel: int, pwm: float, *, start_pos: Optional[float] = None) -> float:
        """Update goal in PWM degrees. Returns current slewed position."""
        with self._lock:
            if channel not in self._channels:
                seed = float(start_pos if start_pos is not None else pwm)
                self._channels[channel] = _ChannelState(pos=seed, goal=seed, vel=0.0)
            st = self._channels[channel]
            st.goal = float(pwm)
            self._ensure_thread_unlocked()
            return st.pos

    def freeze(self, channel: int) -> float:
        """Stop motion: goal ← pos, vel ← 0 (armStop / deadman)."""
        with self._lock:
            st = self._channels.get(channel)
            if st is None:
                return 0.0
            st.goal = st.pos
            st.vel = 0.0
            return st.pos

    def freeze_all(self) -> None:
        with self._lock:
            for st in self._channels.values():
                st.goal = st.pos
                st.vel = 0.0

    def position(self, channel: int) -> Optional[float]:
        with self._lock:
            st = self._channels.get(channel)
            return None if st is None else st.pos

    def goal(self, channel: int) -> Optional[float]:
        with self._lock:
            st = self._channels.get(channel)
            return None if st is None else st.goal

    def apply_immediate(self, channel: int, pwm: float) -> float:
        """Bypass slew (tests / emergency). Still records state."""
        with self._lock:
            p = float(pwm)
            self._channels[channel] = _ChannelState(pos=p, goal=p, vel=0.0)
            apply = self._apply
        if apply is not None:
            apply(channel, p)
        return p

    def tick(self, dt: Optional[float] = None) -> None:
        """Advance all channels by ``dt`` seconds (or elapsed wall time)."""
        with self._lock:
            now = time.monotonic()
            if dt is None:
                dt = max(1e-3, min(0.1, now - self._last_tick))
            self._last_tick = now
            vmax = max_deg_s()
            amax = max_deg_s2()
            apply = self._apply
            updates = []
            for ch, st in self._channels.items():
                err = st.goal - st.pos
                if abs(err) < 0.25 and abs(st.vel) < 1.0:
                    if st.pos != st.goal or st.vel != 0.0:
                        st.pos = st.goal
                        st.vel = 0.0
                        updates.append((ch, st.pos))
                    continue
                # Desired velocity: proportional near goal, capped at vmax.
                desired_v = max(-vmax, min(vmax, err * 8.0))
                if amax <= 0:
                    st.vel = desired_v
                else:
                    dv = desired_v - st.vel
                    max_dv = amax * dt
                    if dv > max_dv:
                        st.vel += max_dv
                    elif dv < -max_dv:
                        st.vel -= max_dv
                    else:
                        st.vel = desired_v
                step = st.vel * dt
                if abs(step) > abs(err):
                    st.pos = st.goal
                    st.vel = 0.0
                else:
                    st.pos += step
                updates.append((ch, st.pos))
        if apply is not None:
            for ch, pos in updates:
                try:
                    apply(ch, pos)
                except Exception:
                    logger.debug("servo slew apply ch%s failed", ch, exc_info=True)

    def _ensure_thread_unlocked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._last_tick = time.monotonic()

        def _loop() -> None:
            period = 1.0 / _TICK_HZ
            while not self._stop.wait(period):
                if not slew_enabled():
                    continue
                try:
                    self.tick()
                except Exception:
                    logger.debug("servo slew tick failed", exc_info=True)

        self._thread = threading.Thread(target=_loop, name="anx-servo-slew", daemon=True)
        self._thread.start()
        logger.info(
            "servo slew started (max=%.0f°/s accel=%.0f°/s² hz=%.0f)",
            max_deg_s(),
            max_deg_s2(),
            _TICK_HZ,
        )

    def start(self) -> None:
        with self._lock:
            self._ensure_thread_unlocked()

    def stop(self) -> None:
        self._stop.set()


_slew = ServoSlewController()


def get_slew() -> ServoSlewController:
    return _slew


def step_toward(
    current: float,
    goal: float,
    *,
    dt: float,
    vmax: float,
    vel: float = 0.0,
    amax: float = 0.0,
) -> tuple[float, float]:
    """Pure helper for unit tests: one integration step → (pos, vel)."""
    err = goal - current
    if abs(err) < 0.25 and abs(vel) < 1.0:
        return goal, 0.0
    desired_v = max(-vmax, min(vmax, err * 8.0))
    if amax <= 0:
        vel = desired_v
    else:
        dv = desired_v - vel
        max_dv = amax * dt
        if dv > max_dv:
            vel += max_dv
        elif dv < -max_dv:
            vel -= max_dv
        else:
            vel = desired_v
    step = vel * dt
    if abs(step) >= abs(err):
        return goal, 0.0
    return current + step, vel
