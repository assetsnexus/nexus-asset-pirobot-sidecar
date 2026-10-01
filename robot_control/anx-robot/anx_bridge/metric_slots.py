"""Generic metric slots keyed by data type — not by vendor part name.

Aligns with region `Metric.dataType` plus unit/role hints used by the portal
3D channel system (range/revolute, boolean IO, number/code, continuous speed):

  range   — numeric span with rest/min/max (servo-like angles)
  boolean — IO on/off (lamps, switches)
  number  — raw numeric value
  code    — enum / mode string
  speed   — signed continuous rate (m/s), integrated by the 3D joint layer

Dry-run / no-hardware tracking updates these slots. Action→slot bindings are
the only place vendor command names appear; storage and publish stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class MetricKind(str, Enum):
    RANGE = "range"
    BOOLEAN = "boolean"
    NUMBER = "number"
    CODE = "code"
    SPEED = "speed"


@dataclass
class MetricSlot:
    metric_id: str
    kind: MetricKind
    value: Any = None
    rest: float = 0.0
    min_v: Optional[float] = None
    max_v: Optional[float] = None
    unit: str = ""
    # Telemetry alias published on the MQTT body (protocol jsonPath).
    publish_key: str = ""

    def clamp_number(self, n: float) -> float:
        if self.min_v is not None:
            n = max(self.min_v, n)
        if self.max_v is not None:
            n = min(self.max_v, n)
        return n


@dataclass
class ActionEffect:
    """How a vendor action mutates one generic slot."""

    metric_id: str
    # set | nudge | toggle | reset
    op: str
    delta: float = 0.0
    value: Any = None


@dataclass
class MetricStore:
    slots: dict[str, MetricSlot] = field(default_factory=dict)
    effects: dict[str, list[ActionEffect]] = field(default_factory=dict)

    def define(self, slot: MetricSlot) -> None:
        if slot.value is None:
            if slot.kind is MetricKind.BOOLEAN:
                slot.value = False
            elif slot.kind is MetricKind.CODE:
                slot.value = ""
            elif slot.kind is MetricKind.RANGE:
                slot.value = float(slot.rest)
            else:
                slot.value = 0.0
        if not slot.publish_key:
            slot.publish_key = slot.metric_id.rsplit(".", 1)[-1]
        self.slots[slot.metric_id] = slot

    def bind(self, action: str, *effects: ActionEffect) -> None:
        self.effects[action] = list(effects)

    def apply_action(self, action: str) -> None:
        for effect in self.effects.get(action, ()):
            slot = self.slots.get(effect.metric_id)
            if slot is None:
                continue
            if effect.op == "reset":
                if slot.kind is MetricKind.BOOLEAN:
                    slot.value = False
                elif slot.kind is MetricKind.CODE:
                    slot.value = ""
                else:
                    slot.value = float(slot.rest)
            elif effect.op == "set":
                if slot.kind is MetricKind.BOOLEAN:
                    slot.value = bool(effect.value)
                elif slot.kind is MetricKind.CODE:
                    slot.value = effect.value
                else:
                    slot.value = slot.clamp_number(float(effect.value))
            elif effect.op == "toggle":
                slot.value = not bool(slot.value)
            elif effect.op == "nudge" and slot.kind in (
                MetricKind.RANGE,
                MetricKind.NUMBER,
                MetricKind.SPEED,
            ):
                cur = float(slot.value or 0.0)
                slot.value = slot.clamp_number(cur + float(effect.delta))

    def set_number(self, metric_id: str, value: float) -> None:
        slot = self.slots.get(metric_id)
        if slot is None:
            return
        slot.value = slot.clamp_number(float(value))

    def set_bool(self, metric_id: str, value: bool) -> None:
        slot = self.slots.get(metric_id)
        if slot is None:
            return
        slot.value = bool(value)

    def get(self, metric_id: str, default: Any = None) -> Any:
        slot = self.slots.get(metric_id)
        if slot is None:
            return default
        return slot.value

    def publish_map(self) -> dict[str, Any]:
        """Flat telemetry keys (jsonPath aliases) for MQTT / dry-run."""
        out: dict[str, Any] = {}
        for slot in self.slots.values():
            key = slot.publish_key or slot.metric_id
            if slot.kind is MetricKind.BOOLEAN:
                out[key] = bool(slot.value)
            elif slot.kind is MetricKind.CODE:
                out[key] = slot.value
            else:
                try:
                    out[key] = float(slot.value)
                except (TypeError, ValueError):
                    out[key] = slot.value
        return out


# Canonical metric ids match region protocol map / portal joint mappings.
SERVO_ARM = "robot.servo.arm_deg"
SERVO_HAND = "robot.servo.hand_deg"
SERVO_LOOK = "robot.servo.look_deg"
SERVO_GRAB = "robot.servo.grab_deg"
SERVO_CAMERA = "robot.servo.camera_deg"
LIGHT_1 = "robot.lights.switch_1"
LIGHT_2 = "robot.lights.switch_2"
LIGHT_3 = "robot.lights.switch_3"
LIGHT_POLICE = "robot.lights.police"
SPEED_LEFT = "robot.drive.motor.left.speed_mps"
SPEED_RIGHT = "robot.drive.motor.right.speed_mps"


def build_rasptank_metric_store(step_deg: float = 8.0) -> MetricStore:
    """Vendor action bindings → generic slots for the Adeept RaspTank overlay."""
    from .servo_limits import RASPTANK_SERVO_LIMITS

    store = MetricStore()
    channel_to_metric = {
        0: (SERVO_ARM, "servo_arm_deg"),
        1: (SERVO_HAND, "servo_hand_deg"),
        2: (SERVO_LOOK, "servo_look_deg"),
        3: (SERVO_GRAB, "servo_grab_deg"),
        4: (SERVO_CAMERA, "servo_camera_deg"),
    }
    for channel, (metric_id, publish_key) in channel_to_metric.items():
        lim = RASPTANK_SERVO_LIMITS[channel]
        rest = float(lim.rest_deg)
        store.define(
            MetricSlot(
                metric_id=metric_id,
                kind=MetricKind.RANGE,
                rest=rest,
                min_v=float(lim.min_deg),
                max_v=float(lim.max_deg),
                unit="deg",
                publish_key=publish_key,
            )
        )
    for metric_id, publish_key in (
        (LIGHT_1, "switch_1"),
        (LIGHT_2, "switch_2"),
        (LIGHT_3, "switch_3"),
        (LIGHT_POLICE, "lights_police"),
    ):
        store.define(
            MetricSlot(
                metric_id=metric_id,
                kind=MetricKind.BOOLEAN,
                publish_key=publish_key,
            )
        )
    for metric_id, publish_key in (
        (SPEED_LEFT, "motor_left_speed_mps"),
        (SPEED_RIGHT, "motor_right_speed_mps"),
    ):
        store.define(
            MetricSlot(
                metric_id=metric_id,
                kind=MetricKind.SPEED,
                unit="m/s",
                publish_key=publish_key,
            )
        )

    # Range nudges (servo-like). Hardware samples override via set_number.
    store.bind("armUp", ActionEffect(SERVO_ARM, "nudge", delta=step_deg))
    store.bind("armDown", ActionEffect(SERVO_ARM, "nudge", delta=-step_deg))
    store.bind("handUp", ActionEffect(SERVO_HAND, "nudge", delta=-step_deg))
    store.bind("handDown", ActionEffect(SERVO_HAND, "nudge", delta=step_deg))
    store.bind("lookleft", ActionEffect(SERVO_LOOK, "nudge", delta=step_deg))
    store.bind("lookright", ActionEffect(SERVO_LOOK, "nudge", delta=-step_deg))
    store.bind("grab", ActionEffect(SERVO_GRAB, "nudge", delta=step_deg))
    store.bind("loose", ActionEffect(SERVO_GRAB, "nudge", delta=-step_deg))
    store.bind("up", ActionEffect(SERVO_CAMERA, "nudge", delta=-step_deg))
    store.bind("down", ActionEffect(SERVO_CAMERA, "nudge", delta=step_deg))
    store.bind(
        "home",
        ActionEffect(SERVO_ARM, "reset"),
        ActionEffect(SERVO_HAND, "reset"),
        ActionEffect(SERVO_LOOK, "reset"),
        ActionEffect(SERVO_GRAB, "reset"),
        ActionEffect(SERVO_CAMERA, "reset"),
    )

    # Boolean IO
    store.bind("Switch_1_on", ActionEffect(LIGHT_1, "set", value=True))
    store.bind("Switch_1_off", ActionEffect(LIGHT_1, "set", value=False))
    store.bind("Switch_2_on", ActionEffect(LIGHT_2, "set", value=True))
    store.bind("Switch_2_off", ActionEffect(LIGHT_2, "set", value=False))
    store.bind("Switch_3_on", ActionEffect(LIGHT_3, "set", value=True))
    store.bind("Switch_3_off", ActionEffect(LIGHT_3, "set", value=False))
    store.bind("police", ActionEffect(LIGHT_POLICE, "set", value=True))
    store.bind("police_off", ActionEffect(LIGHT_POLICE, "set", value=False))
    return store
