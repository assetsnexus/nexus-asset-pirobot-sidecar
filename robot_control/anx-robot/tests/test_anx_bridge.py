"""Unit tests for the ANX overlay MQTT bridge (no broker / GPIO required)."""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time

import pytest

_OVERLAY = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _OVERLAY)

from anx_bridge.command_router import CommandRouter
from anx_bridge.config import BridgeConfig, BridgeConfigError, OverlayMotionConfig
from anx_bridge.controller_input import (
    ABS_X,
    BTN_SOUTH,
    apply_controller_message,
    apply_deadzone,
    axis_to_action,
    axis_to_actions,
    load_map,
    select_source,
)
from anx_bridge.deadman import Deadman
from anx_bridge.motion import DEFAULT_SEQUENCE, MotionController
from anx_bridge.mqtt_bridge import MqttBridge
from anx_bridge.odometry import (
    drive_duration_s,
    speed_mps,
    turn_90_duration_s,
    wheel_circumference_m,
)
from anx_bridge.sensors import SensorSuite
from anx_bridge.telemetry import build_telemetry


def test_router_known_and_stop():
    r = CommandRouter()
    assert r.execute("forward")
    assert not r.execute("nope")
    r.all_stop()
    assert ("DS", None) in r.calls


def test_router_accepts_motion_actions():
    r = CommandRouter()
    assert r.execute("turn_left_90")
    assert r.execute("drive_cm", 50)
    assert r.execute("drive_sequence", steps=DEFAULT_SEQUENCE)
    assert r.execute("police")
    assert r.execute("police_off")
    assert r.execute("stop")


def test_deadzone_and_axis():
    assert apply_deadzone(0.05, 0.15) == 0.0
    mapping = {
        "deadzone": 0.15,
        "axes": {"ABS_Y": {"negative": "forward", "positive": "backward", "speed": True}},
    }
    action, value = axis_to_action(mapping, "ABS_Y", -0.8)
    assert action == "wsB"
    assert value > 50
    actions = axis_to_actions(mapping, "ABS_Y", -0.8)
    assert actions[0][0] == "wsB"
    assert actions[1] == ("forward", None)


def test_source_selection():
    pad = {"abs_codes": [ABS_X], "key_codes": [BTN_SOUTH]}
    assert select_source([pad], "auto") == "usb"
    assert select_source([], "auto") == "mqtt"
    assert select_source([pad], "mqtt") == "mqtt"


def test_deadman_trips():
    r = CommandRouter()
    d = Deadman(r, 500)
    d.last_input = 0
    assert d.tick(now=1.0)
    assert d.trips == 1
    assert ("DS", None) in r.calls


def test_deadman_skips_during_timed_motion():
    r = CommandRouter()
    d = Deadman(r, 500)
    d.last_input = 0
    d.set_timed_motion_active(True)
    assert not d.tick(now=1.0)
    assert d.trips == 0
    d.set_timed_motion_active(False)
    assert d.tick(now=1.0)
    assert d.trips == 1


def test_deadman_node_heartbeat_suppresses_quiet_trip():
    r = CommandRouter()
    d = Deadman(r, 500, node_heartbeat_timeout_ms=2000)
    d.last_input = 0
    d.poke_node_heartbeat()
    # Quiet MQTT would trip, but heartbeat is fresh.
    assert not d.tick(now=d.last_node_heartbeat + 0.1)
    # Heartbeat stale → failsafe stop.
    assert d.tick(now=d.last_node_heartbeat + 3.0)
    assert d.trips == 1


def test_telemetry_shape():
    body = build_telemetry(
        control_source="mqtt",
        deadman_trips=2,
        sensors={"ultrasonic_mm": 250.0, "battery_voltage_v": 7.8, "battery_percent": 75.0},
        state={"speed_mps": 0.1, "distance_m": 1.2, "odometry_source": "open_loop_pwm"},
    )
    assert body["control_source"] == "mqtt"
    assert body["deadman_trips_total"] == 2
    assert "ultrasonic_distance_cm" in body
    assert body["ultrasonic_mm"] == 250.0
    assert body["distance_mm"] == 250.0
    assert body["speed_mps"] == 0.1
    assert body["distance_m"] == 1.2
    assert "motor_left_speed_mps" in body
    assert "motor_right_speed_mps" in body
    assert "servo_arm_deg" in body
    assert "servo_camera_deg" in body
    assert "lights_police" in body
    assert body["odometry_source"] == "open_loop_pwm"
    assert body["battery_voltage_v"] == 7.8


def test_side_speeds_are_signed_per_motor():
    from anx_bridge.odometry import side_speeds_mps

    left, right = side_speeds_mps(100, "forward", "no", 0.35)
    assert left == pytest.approx(0.35)
    assert right == pytest.approx(0.35)
    left, right = side_speeds_mps(100, "backward", "no", 0.35)
    assert left == pytest.approx(-0.35)
    assert right == pytest.approx(-0.35)
    left, right = side_speeds_mps(100, "forward", "left", 0.35)
    assert left == pytest.approx(-0.35)
    assert right == pytest.approx(0.35)
    left, right = side_speeds_mps(0, "forward", "no", 0.35)
    assert left == 0
    assert right == 0


def test_wheel_circumference_and_turn_time():
    circ = wheel_circumference_m(0.045)
    assert circ == pytest.approx(math.pi * 0.045)
    v = speed_mps(100, 0.35)
    assert v == pytest.approx(0.35)
    t = turn_90_duration_s(0.12, 0.35)
    assert t == pytest.approx((math.pi / 2.0) * (0.12 / 2.0) / 0.35)
    assert drive_duration_s(1.0, 0.35) == pytest.approx(1.0 / 0.35)


def test_mqtt_cmd():
    r = CommandRouter()
    cfg = BridgeConfig.from_env(
        {"ANX_BRIDGE_ENABLED": "true", "ANX_ASSET_ID": "a1", "ANX_CONTROL_SOURCE": "mqtt"}
    )
    published = []
    bridge = MqttBridge(cfg, r, publish=lambda t, p, retain: published.append((t, p, retain)))
    bridge.on_message("anx/asset/a1/cmd", '{"action":"forward"}')
    assert r.last_action == "forward"
    bridge.publish_telemetry({"cpu_percent": 1})
    assert published[0][0].endswith("/telemetry")


def test_mqtt_drive_sequence_passes_steps():
    seen = []

    def exec_fn(action, value=None, steps=None):
        seen.append((action, value, steps))

    r = CommandRouter(exec_fn)
    cfg = BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "true", "ANX_ASSET_ID": "a1"})
    bridge = MqttBridge(cfg, r, publish=lambda *args: None)
    steps = [{"action": "drive_cm", "value": 100}, {"action": "turn_right_90"}]
    bridge.on_message(
        "anx/asset/a1/cmd",
        json.dumps({"action": "drive_sequence", "steps": steps}),
    )
    assert seen[-1][0] == "drive_sequence"
    assert seen[-1][2] == steps


def test_mqtt_telemetry_fast_topic():
    cfg = BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "true", "ANX_TOPIC_PREFIX": "rasptank"})
    published = []
    bridge = MqttBridge(cfg, CommandRouter(), publish=lambda t, p, retain: published.append(t))
    bridge.publish_telemetry_fast({"ultrasonic_mm": 80})
    assert published[0] == "rasptank/telemetry_fast"


def test_mqtt_node_heartbeat():
    poked = []
    cfg = BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "true", "ANX_TOPIC_PREFIX": "rasptank"})
    bridge = MqttBridge(
        cfg,
        CommandRouter(),
        publish=lambda *a: None,
        on_node_heartbeat=lambda: poked.append(1),
    )
    bridge.on_message("rasptank/node_heartbeat", "{}")
    assert poked == [1]


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += max(0.0, float(dt))


def _wait_motion(motion: MotionController, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while motion.active and time.time() < deadline:
        time.sleep(0.01)
    time.sleep(0.02)


def test_motion_sequence_order():
    clock = _FakeClock()
    events: list[str] = []
    ultra = {"mm": 500.0}

    motion = MotionController(
        drive_forward=lambda pwm: events.append(f"fwd:{pwm}"),
        drive_backward=lambda pwm: events.append(f"back:{pwm}"),
        spin_left=lambda pwm: events.append(f"left:{pwm}"),
        spin_right=lambda pwm: events.append(f"right:{pwm}"),
        motor_stop=lambda: events.append("stop"),
        get_pwm=lambda: 100,
        read_ultrasonic_mm=lambda: ultra["mm"],
        set_speed_mps=lambda v: None,
        accumulate_distance=lambda v, dt: None,
        track_width_m=0.12,
        speed_at_full_pwm_mps=0.35,
        obstacle_stop_mm=100.0,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )
    steps = [
        {"action": "drive_cm", "value": 10},
        {"action": "turn_right_90"},
        {"action": "drive_cm", "value": 20},
        {"action": "turn_left_90"},
    ]
    motion.start_sequence(steps)
    _wait_motion(motion)
    drive_events = [e for e in events if e != "stop"]
    assert drive_events == ["fwd:100", "right:100", "fwd:100", "left:100"]
    assert events[-1] == "stop"


def test_motion_obstacle_stop_at_100mm():
    clock = _FakeClock()
    events: list[str] = []
    speeds: list[float] = []
    ultra = {"mm": 50.0}  # below 100 mm threshold

    motion = MotionController(
        drive_forward=lambda pwm: events.append("fwd"),
        drive_backward=lambda pwm: events.append("back"),
        spin_left=lambda pwm: events.append("left"),
        spin_right=lambda pwm: events.append("right"),
        motor_stop=lambda: events.append("stop"),
        get_pwm=lambda: 100,
        read_ultrasonic_mm=lambda: ultra["mm"],
        set_speed_mps=lambda v: speeds.append(v),
        accumulate_distance=lambda v, dt: None,
        track_width_m=0.12,
        speed_at_full_pwm_mps=0.35,
        obstacle_stop_mm=100.0,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )
    motion.start_drive_cm(200)
    _wait_motion(motion)
    assert motion.last_obstacle_stop is True
    assert "fwd" in events
    assert "stop" in events
    assert 0.0 in speeds


def test_stop_is_idempotent():
    clock = _FakeClock()
    stops = []
    motion = MotionController(
        drive_forward=lambda pwm: None,
        drive_backward=lambda pwm: None,
        spin_left=lambda pwm: None,
        spin_right=lambda pwm: None,
        motor_stop=lambda: stops.append(1),
        get_pwm=lambda: 60,
        read_ultrasonic_mm=lambda: 500.0,
        set_speed_mps=lambda v: None,
        accumulate_distance=lambda v, dt: None,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )
    motion.stop()
    motion.stop()
    assert len(stops) >= 2


def test_new_motion_cancels_previous():
    clock = _FakeClock()
    started = threading.Event()
    events: list[str] = []

    def slow_sleep(dt: float) -> None:
        started.set()
        end = time.time() + min(dt, 0.3)
        while time.time() < end:
            time.sleep(0.01)
            clock.t += 0.01

    motion = MotionController(
        drive_forward=lambda pwm: events.append("fwd"),
        drive_backward=lambda pwm: None,
        spin_left=lambda pwm: events.append("left"),
        spin_right=lambda pwm: None,
        motor_stop=lambda: events.append("stop"),
        get_pwm=lambda: 100,
        read_ultrasonic_mm=lambda: 500.0,
        set_speed_mps=lambda v: None,
        accumulate_distance=lambda v, dt: None,
        speed_at_full_pwm_mps=0.05,
        sleep_fn=slow_sleep,
        monotonic_fn=time.monotonic,
    )
    motion.start_drive_cm(500)
    assert started.wait(1.0)
    motion.start_turn_left_90()
    _wait_motion(motion)
    assert "left" in events


def test_sensor_suite_injectable():
    suite = SensorSuite(
        ultrasonic_mm_fn=lambda: 123.0,
        battery_fn=lambda: (7.5, 62.5),
        line_fn=lambda: (1, 0, 1),
    )
    sample = suite.sample()
    assert sample["ultrasonic_mm"] == 123.0
    assert sample["distance_mm"] == 123.0
    assert sample["battery_voltage_v"] == 7.5
    assert sample["line_left"] == 1


def test_plaintext_mqtt_is_rejected():
    with pytest.raises(BridgeConfigError) as exc:
        BridgeConfig.from_env(
            {"ANX_BRIDGE_ENABLED": "true", "ANX_MQTT_URL": "mqtt://127.0.0.1:1883"}
        )
    assert "plaintext" in str(exc.value)


def test_ipc_mqtt_broker_env():
    cfg = BridgeConfig.from_env(
        {
            "ANX_BRIDGE_ENABLED": "true",
            "MQTT_BROKER": "mqtts://mqtt:8883",
            "MQTT_USER": "anx",
            "MQTT_PASSWORD": "secret",
            "MQTT_CA_FILE": "/certs/ca.crt",
            "ANX_TOPIC_PREFIX": "rasptank",
        }
    )
    assert cfg.mqtt_tls is True
    assert cfg.mqtt_host == "mqtt"
    assert cfg.mqtt_port == 8883
    assert cfg.mqtt_user == "anx"
    assert cfg.mqtt_password == "secret"
    assert cfg.mqtt_ca == "/certs/ca.crt"
    assert cfg.topic_prefix == "rasptank"
    assert cfg.motion.wheel_diameter_m == 0.045
    assert cfg.motion.track_width_m == 0.12
    assert cfg.motion.speed_at_full_pwm_mps == 0.35
    assert cfg.motion.obstacle_stop_mm == 100.0


def test_default_url_is_tls():
    cfg = BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "true", "ANX_ASSET_ID": "a1"})
    assert cfg.mqtt_tls is True
    assert cfg.mqtt_port == 8883


def test_paho_session_sets_tls(tmp_path):
    from anx_bridge.mqtt_client import PahoSession

    ca = tmp_path / "ca.crt"
    ca.write_text("dummy-ca")

    class Fake:
        def __init__(self):
            self.tls = None
            self.connected = None
            self.started = False
            self.subs = []

        def tls_set(self, **kwargs):
            self.tls = kwargs

        def tls_insecure_set(self, flag):
            self.insecure = flag

        def username_pw_set(self, user, password):
            self.user = user

        def will_set(self, *args, **kwargs):
            pass

        def reconnect_delay_set(self, **kwargs):
            pass

        def subscribe(self, topic):
            self.subs.append(topic)

        def connect_async(self, host, port, keepalive=30):
            self.connected = (host, port)

        def loop_start(self):
            self.started = True

        def publish(self, *args, **kwargs):
            pass

    cfg = BridgeConfig.from_env(
        {
            "ANX_BRIDGE_ENABLED": "true",
            "MQTT_BROKER": "mqtts://10.1.0.8:8883",
            "MQTT_CA_FILE": str(ca),
            "MQTT_USER": "anx",
            "MQTT_PASSWORD": "pw",
        }
    )
    fake = Fake()
    session = PahoSession(cfg, lambda topic, payload: None, client=fake)
    session.start()
    assert fake.tls["ca_certs"] == str(ca)
    assert fake.insecure is False
    assert fake.connected == ("10.1.0.8", 8883)
    assert fake.started
    assert any(topic.endswith("/cmd") for topic in fake.subs)


def test_controller_message_drives_router():
    mapping = load_map(
        os.path.join(os.path.dirname(__file__), "..", "anx_bridge", "controller_map.xbox.json")
    )
    router = CommandRouter()
    apply_controller_message(mapping, {"axes": {"ly": -0.9}, "buttons": {}}, router)
    assert any(a == "wsB" and isinstance(v, int) and v > 50 for a, v in router.calls)
    assert ("forward", None) in router.calls


def test_controller_button_aliases():
    mapping = load_map(
        os.path.join(os.path.dirname(__file__), "..", "anx_bridge", "controller_map.xbox.json")
    )
    router = CommandRouter()
    apply_controller_message(mapping, {"axes": {}, "buttons": {"a": True}}, router)
    assert router.last_action == "grab"


def test_executor_receives_mqtt_command():
    seen = []
    router = CommandRouter(lambda action, value: seen.append((action, value)))
    cfg = BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "true", "ANX_ASSET_ID": "a1"})
    bridge = MqttBridge(cfg, router, publish=lambda *args: None)
    bridge.on_message("anx/asset/a1/cmd", '{"action":"forward"}')
    assert seen == [("forward", None)]


def test_config_rejects_bad_source():
    with pytest.raises(BridgeConfigError):
        BridgeConfig.from_env({"ANX_CONTROL_SOURCE": "bluetooth"})


def test_overlay_motion_config_defaults():
    m = OverlayMotionConfig()
    assert m.wheel_diameter_m == 0.045
    assert m.track_width_m == 0.12
    assert m.speed_at_full_pwm_mps == 0.35


def test_bridge_disabled_is_noop():
    from anx_bridge.bridge import start_bridge

    # Must not raise when disabled
    start_bridge(config=BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "false"}))


def test_arm_rest_deg_clamped(monkeypatch):
    from anx_bridge.servos import arm_rest_deg, park_arm_upright

    monkeypatch.delenv("ANX_ARM_REST_DEG", raising=False)
    assert arm_rest_deg() == 0
    monkeypatch.setenv("ANX_ARM_REST_DEG", "200")
    assert arm_rest_deg() == 180
    monkeypatch.setenv("ANX_ARM_REST_DEG", "-10")
    assert arm_rest_deg() == 0

    class _Fake:
        def __init__(self):
            self.initPos = [90, 90, 90, 90, 90]
            self.nowPos = [90, 90, 90, 90, 90]
            self.calls = []

        def setPWM(self, channel, deg):
            self.nowPos[channel] = deg
            self.calls.append((channel, deg))

    fake = _Fake()
    park_arm_upright(fake, deg=0)
    assert fake.calls == [(0, 0)]
    assert fake.initPos[0] == 0


def test_servos_idle_until_armed(monkeypatch):
    """PWM stays released until ensure_servos_armed / stay released on disconnect."""
    import anx_bridge.servos as servos

    monkeypatch.delenv("ANX_ARM_REST_DEG", raising=False)

    class _Ch:
        def __init__(self):
            self.duty_cycle = 4095

    class _Pwm:
        def __init__(self):
            self.channels = [_Ch() for _ in range(8)]

    class _Fake:
        def __init__(self):
            self.pwm_servo = _Pwm()
            self.initPos = [90] * 8
            self.nowPos = [90] * 8
            self.set_calls = []

        def setPWM(self, channel, deg):
            self.nowPos[channel] = deg
            self.set_calls.append((channel, deg))
            self.pwm_servo.channels[channel].duty_cycle = 3000

    # Reset module gate between tests.
    servos._armed = False  # noqa: SLF001
    servos._primary_ctrl = None  # noqa: SLF001

    fake = _Fake()
    servos.register_servo_ctrl(fake)
    servos.release_servos(fake)
    assert servos.servos_armed() is False
    assert all(ch.duty_cycle == 0 for ch in fake.pwm_servo.channels)
    assert fake.set_calls == []

    servos.ensure_servos_armed(fake, park_arm=True)
    assert servos.servos_armed() is True
    assert fake.set_calls == [(0, 0)]
    assert fake.initPos[0] == 0

    # Idempotent while armed.
    servos.ensure_servos_armed(fake, park_arm=True)
    assert fake.set_calls == [(0, 0)]

    servos.release_servos_if_idle(connected=True)
    assert servos.servos_armed() is True
    servos.release_servos_if_idle(connected=False)
    assert servos.servos_armed() is False
    assert all(ch.duty_cycle == 0 for ch in fake.pwm_servo.channels)


def test_set_control_socket_clients_arms_and_releases(monkeypatch):
    import anx_bridge.bridge as bridge
    import anx_bridge.servos as servos

    class _Ch:
        def __init__(self):
            self.duty_cycle = 100

    class _Fake:
        def __init__(self):
            self.pwm_servo = type("P", (), {"channels": [_Ch() for _ in range(8)]})()
            self.initPos = [90] * 8
            self.nowPos = [90] * 8
            self.parked = []

        def setPWM(self, channel, deg):
            self.nowPos[channel] = deg
            self.parked.append((channel, deg))
            self.pwm_servo.channels[channel].duty_cycle = 2000

    servos._armed = False  # noqa: SLF001
    servos._primary_ctrl = None  # noqa: SLF001
    fake = _Fake()
    servos.register_servo_ctrl(fake)
    servos.release_servos(fake)

    # Bridge may have no status lights in unit context.
    bridge._status_lights = None  # noqa: SLF001
    bridge.set_control_socket_clients(1)
    assert servos.servos_armed() is True
    assert fake.parked == [(0, 0)]

    bridge.set_control_socket_clients(0)
    assert servos.servos_armed() is False
    assert all(ch.duty_cycle == 0 for ch in fake.pwm_servo.channels)


def test_status_lights_red_disconnected_blue_connected():
    from anx_bridge.idle_police import StatusLightsController

    class _Hw:
        def __init__(self):
            self.colors = []

        def set_status_blink(self, color):
            self.colors.append(color)

    clock = {"t": 0.0}
    hw = _Hw()
    lights = StatusLightsController(hw, idle_ms=1000, clock=lambda: clock["t"])
    lights.set_ws_connected(False)
    assert hw.colors[-1] == "red"
    lights.set_ws_connected(True)
    assert hw.colors[-1] == "blue"
    lights.note_light_override()
    clock["t"] = 0.5
    lights.tick()
    assert lights.manual is True
    clock["t"] = 1.5
    lights.set_ws_connected(False)
    lights.tick()
    assert lights.manual is False
    assert hw.colors[-1] == "red"


def test_idle_police_turns_on_after_quiet():
    # Compat: IdlePoliceIndicator alias still constructs StatusLightsController.
    from anx_bridge.idle_police import IdlePoliceIndicator

    class _Hw:
        def __init__(self):
            self.modes = []

        def set_status_blink(self, color):
            self.modes.append(color)

        def set_idle_police(self, active: bool) -> None:
            self.modes.append(active)

    clock = {"t": 0.0}
    hw = _Hw()
    idle = IdlePoliceIndicator(hw, idle_ms=1000, clock=lambda: clock["t"])
    idle.set_ws_connected(False)
    assert "red" in hw.modes
