"""Absolute servo-angle set/get with rate-limited slew (overlay; vendor tree untouched)."""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from .servo_limits import all_limits, servo_limit
from .servo_slew import get_slew, slew_enabled
from .servos import ensure_servos_armed, register_servo_ctrl

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_ctrl: Any = None
_goals_ui: Dict[int, int] = {}  # commanded logical angle (slider / hold target)
_actual_ui: Dict[int, int] = {}  # last applied logical angle from slewer
_apply_wired = False

# Hold-to-move → (channel, toward_high_ui). Shoulder is special-cased in
# hold_servo_action (armUp → rest/upright PWM 0, armDown → capped forward).
_HOLD_HIGH: Dict[str, tuple[int, bool]] = {
    "handUp": (1, False),  # vendor singleServo(1, -1)
    "handDown": (1, True),
    "lookleft": (2, True),
    "lookright": (2, False),
    "grab": (3, True),
    "loose": (3, False),
    "up": (4, False),
    "down": (4, True),
}

_STOP_CHANNELS: Dict[str, tuple[int, ...]] = {
    "armStop": (0,),
    "handStop": (1,),
    "LRstop": (2,),
    "GLstop": (3,),
    "UDstop": (4,),
}


def _wire_slew_apply() -> None:
    global _apply_wired
    if _apply_wired:
        return
    get_slew().set_apply(_apply_pwm_from_slew)
    _apply_wired = True


def _apply_pwm_from_slew(channel: int, pwm: float) -> None:
    """Background slewer callback — write PCA9685 and track actual UI."""
    try:
        lim = servo_limit(channel)
    except KeyError:
        return
    pwm_i = max(lim.min_deg, min(lim.max_deg, int(round(pwm))))
    ui = lim.pwm_to_ui(pwm_i)
    with _lock:
        sc = _ctrl
        _actual_ui[channel] = ui
        if sc is None:
            return
        try:
            if hasattr(sc, "setPWM"):
                sc.setPWM(channel, pwm_i)
            elif hasattr(sc, "set_angle"):
                sc.set_angle(channel, pwm_i)
        except Exception:
            logger.debug("slew apply setPWM ch%s failed", channel, exc_info=True)


def _seed_ui_from_ctrl(servo_ctrl: Any, channel: int) -> int:
    lim = servo_limit(channel)
    seeded_pwm = None
    for attr in ("nowPos", "initPos"):
        arr = getattr(servo_ctrl, attr, None)
        if arr is not None and len(arr) > channel:
            seeded_pwm = int(arr[channel])
            break
    if seeded_pwm is None:
        return lim.rest_deg
    return lim.pwm_to_ui(seeded_pwm)


def bind_servo_ctrl(servo_ctrl: Any) -> None:
    """Remember the live ServoCtrl that owns the PCA9685."""
    global _ctrl
    if servo_ctrl is None:
        return
    _wire_slew_apply()
    slew = get_slew()
    with _lock:
        _ctrl = servo_ctrl
        register_servo_ctrl(servo_ctrl)
        for lim in all_limits():
            if lim.channel in _goals_ui:
                continue
            ui = _seed_ui_from_ctrl(servo_ctrl, lim.channel)
            _goals_ui[lim.channel] = ui
            _actual_ui[lim.channel] = ui
            slew.sync_pos(lim.channel, float(lim.ui_to_pwm(ui)))


def current_angles() -> Dict[int, int]:
    """Commanded (goal) logical angles — what the UI should show."""
    with _lock:
        out: Dict[int, int] = {}
        for lim in all_limits():
            out[lim.channel] = int(_goals_ui.get(lim.channel, lim.rest_deg))
        return out


def actual_angles() -> Dict[int, int]:
    with _lock:
        out: Dict[int, int] = {}
        for lim in all_limits():
            out[lim.channel] = int(
                _actual_ui.get(lim.channel, _goals_ui.get(lim.channel, lim.rest_deg))
            )
        return out


def describe_servos() -> List[dict]:
    goals = current_angles()
    actual = actual_angles()
    return [
        {
            "channel": lim.channel,
            "name": lim.name,
            "deg": goals[lim.channel],
            "actual": actual[lim.channel],
            "min": lim.min_deg,
            "max": lim.max_deg,
            "rest": lim.rest_deg,
            "invert": lim.invert,
            "slew": slew_enabled(),
        }
        for lim in all_limits()
    ]


def set_servo_angle(
    channel: int,
    deg: int,
    *,
    ctrl: Any = None,
    immediate: bool = False,
) -> int:
    """Drive one channel to a logical (slider) angle; rate-limited unless immediate."""
    lim = servo_limit(channel)
    ui = lim.clamp(deg)
    pwm = lim.ui_to_pwm(ui)
    _wire_slew_apply()
    with _lock:
        sc = ctrl if ctrl is not None else _ctrl
        if sc is None:
            raise RuntimeError("no servo controller bound")
        bind_servo_ctrl(sc)
        ensure_servos_armed(sc, park_arm=False)
        _goals_ui[channel] = ui
        use_slew = slew_enabled() and not immediate
        if not use_slew:
            if hasattr(sc, "setPWM"):
                sc.setPWM(channel, pwm)
            elif hasattr(sc, "set_angle"):
                sc.set_angle(channel, pwm)
            else:
                raise RuntimeError("servo ctrl cannot set angle")
            _actual_ui[channel] = ui
            get_slew().sync_pos(channel, float(pwm))
        else:
            start = lim.ui_to_pwm(_actual_ui.get(channel, ui))
            get_slew().set_goal(channel, float(pwm), start_pos=float(start))
        logger.info(
            "servo ch%s (%s) goal UI %s° → PWM %s°%s (limits %s–%s%s)",
            channel,
            lim.name,
            ui,
            pwm,
            " [slew]" if use_slew else " [immediate]",
            lim.min_deg,
            lim.max_deg,
            ", inverted" if lim.invert else "",
        )
        return ui


def hold_servo_action(action: str) -> bool:
    """Map armUp/armDown/… to a slew goal at an endstop."""
    if action in ("armUp", "armDown"):
        lim = servo_limit(0)
        # Upright rest is PWM 0; fold only as far as max_deg (below stall).
        target = lim.rest_deg if action == "armUp" else lim.endstop_away_from_rest()
        set_servo_angle(0, target)
        return True
    spec = _HOLD_HIGH.get(action)
    if spec is None:
        return False
    channel, toward_high = spec
    lim = servo_limit(channel)
    target = lim.max_deg if toward_high else lim.min_deg
    set_servo_angle(channel, target)
    return True


def freeze_servo_action(action: str) -> bool:
    """armStop / handStop / … — freeze slewed channel(s) at current pos."""
    channels = _STOP_CHANNELS.get(action)
    if channels is None:
        return False
    slew = get_slew()
    with _lock:
        for ch in channels:
            try:
                lim = servo_limit(ch)
            except KeyError:
                continue
            pos_pwm = slew.freeze(ch)
            ui = lim.pwm_to_ui(int(round(pos_pwm)))
            _goals_ui[ch] = ui
            _actual_ui[ch] = ui
    # Also stop vendor wiggle thread if still running alongside.
    sc = _ctrl
    if sc is not None and hasattr(sc, "stopWiggle"):
        try:
            sc.stopWiggle()
        except Exception:
            pass
    return True


def freeze_all_servos() -> None:
    """Stop all slewed motion (release / home / disconnect)."""
    slew = get_slew()
    slew.freeze_all()
    with _lock:
        for lim in all_limits():
            pos = slew.position(lim.channel)
            if pos is None:
                continue
            ui = lim.pwm_to_ui(int(round(pos)))
            _goals_ui[lim.channel] = ui
            _actual_ui[lim.channel] = ui


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
