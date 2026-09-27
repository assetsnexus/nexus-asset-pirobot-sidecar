"""USB gamepad reader (evdev) with MQTT fallback selection."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

ABS_X = 0x00
BTN_SOUTH = 0x130


def apply_deadzone(value: float, deadzone: float) -> float:
    if abs(value) < deadzone:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    scaled = (abs(value) - deadzone) / max(1e-6, 1.0 - deadzone)
    return max(-1.0, min(1.0, sign * scaled))


def axis_to_action(mapping: dict, axis_name: str, value: float) -> Optional[tuple[str, Any]]:
    """Return the primary action for an axis (speed wsB when mapped, else direction)."""
    actions = axis_to_actions(mapping, axis_name, value)
    return actions[0] if actions else None


def axis_to_actions(mapping: dict, axis_name: str, value: float) -> list[tuple[str, Any]]:
    """Map an axis to zero or more robot actions (speed + direction when both apply)."""
    spec = (mapping.get("axes") or {}).get(axis_name)
    if not spec:
        return []
    deadzone = float(mapping.get("deadzone", 0.15))
    v = apply_deadzone(value, deadzone)
    if v == 0.0:
        return []
    if v < 0 and spec.get("negative"):
        action = spec["negative"]
    elif v > 0 and spec.get("positive"):
        action = spec["positive"]
    else:
        return []
    out: list[tuple[str, Any]] = []
    if spec.get("speed"):
        out.append(("wsB", int(round(abs(v) * 100))))
    out.append((action, None))
    return out


def button_to_action(mapping: dict, button_name: str, pressed: bool) -> Optional[str]:
    if not pressed:
        return None
    action = (mapping.get("buttons") or {}).get(button_name)
    return action


def list_gamepads() -> list[dict]:
    """evdev gamepads as {abs_codes, key_codes}. Empty when evdev or /dev/input is absent."""
    try:
        import evdev
    except ImportError:
        return []
    found = []
    try:
        paths = evdev.list_devices()
    except OSError:
        return []
    for path in paths:
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            found.append(
                {
                    "name": dev.name,
                    "abs_codes": list(caps.get(evdev.ecodes.EV_ABS, [])),
                    "key_codes": list(caps.get(evdev.ecodes.EV_KEY, [])),
                }
            )
        except OSError:
            continue
    return found


def select_source(devices: list[dict], preferred: str) -> str:
    """devices: [{name, abs_codes, key_codes}]. Returns usb|mqtt."""
    if preferred == "mqtt":
        return "mqtt"
    has_pad = any(
        ABS_X in d.get("abs_codes", []) and BTN_SOUTH in d.get("key_codes", []) for d in devices
    )
    if preferred == "usb":
        return "usb" if has_pad else "mqtt"
    return "usb" if has_pad else "mqtt"


_AXIS_ALIASES = {
    "lx": "ABS_X",
    "ly": "ABS_Y",
    "rx": "ABS_RX",
    "ry": "ABS_RY",
    "rt": "ABS_RZ",
    "lt": "ABS_Z",
}

# ControllerStateMessage button ids (xbox_mqtt preset) → evdev-style map keys
_BUTTON_ALIASES = {
    "a": "BTN_SOUTH",
    "b": "BTN_EAST",
    "x": "BTN_WEST",
    "y": "BTN_NORTH",
    "lb": "BTN_TL",
    "rb": "BTN_TR",
    "start": "BTN_START",
    "select": "BTN_SELECT",
    "back": "BTN_SELECT",
}


def apply_controller_message(mapping: dict, data: dict, router, deadman=None) -> bool:
    """Map a ControllerStateMessage (lx/ly or ABS_*) onto robot actions."""
    acted = False
    for name, value in (data.get("axes") or {}).items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        for action, aval in axis_to_actions(mapping, _AXIS_ALIASES.get(name, name), numeric):
            router.execute(action, aval)
            acted = True
    for name, pressed in (data.get("buttons") or {}).items():
        mapped_name = _BUTTON_ALIASES.get(name, name)
        action = button_to_action(mapping, mapped_name, bool(pressed))
        if not action:
            # Also try the raw name in case the map uses contract ids directly
            action = button_to_action(mapping, name, bool(pressed))
        if not action:
            continue
        if action == "allStop":
            router.all_stop()
        else:
            router.execute(action)
        acted = True
    if acted and deadman is not None:
        deadman.poke()
    return acted


def start_usb_reader(mapping: dict, router, deadman, stop_event) -> None:
    """Daemon thread. Reads the first Xbox-like evdev pad until stop_event is set."""

    def _run() -> None:
        try:
            import evdev
            from evdev import ecodes
        except ImportError:
            logger.warning("evdev is not installed; USB gamepad control is off")
            return
        device = None
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            caps = dev.capabilities()
            abs_codes = caps.get(ecodes.EV_ABS, [])
            key_codes = caps.get(ecodes.EV_KEY, [])
            if ABS_X in abs_codes and BTN_SOUTH in key_codes:
                device = dev
                break
        if device is None:
            logger.info("no USB gamepad found")
            return
        logger.info("USB gamepad %s", device.name)
        for event in device.read_loop():
            if stop_event.is_set():
                break
            if event.type == ecodes.EV_ABS:
                info = device.absinfo(event.code)
                span = (info.max - info.min) / 2 or 1
                mid = (info.max + info.min) / 2
                name = ecodes.ABS.get(event.code, event.code)
                if isinstance(name, list):
                    name = name[0]
                for action, aval in axis_to_actions(mapping, str(name), (event.value - mid) / span):
                    router.execute(action, aval)
                    deadman.poke()
            elif event.type == ecodes.EV_KEY:
                name = ecodes.BTN.get(event.code) or ecodes.KEY.get(event.code) or event.code
                if isinstance(name, list):
                    name = name[0]
                action = button_to_action(mapping, str(name), event.value == 1)
                if action == "allStop":
                    router.all_stop()
                    deadman.poke()
                elif action:
                    router.execute(action)
                    deadman.poke()

    threading.Thread(target=_run, name="anx-usb", daemon=True).start()


def load_map(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
