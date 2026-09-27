"""Unit tests for the ANX overlay MQTT bridge (no broker / GPIO required)."""
from __future__ import annotations

import os
import sys

import pytest

_OVERLAY = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _OVERLAY)

from anx_bridge.command_router import CommandRouter
from anx_bridge.config import BridgeConfig, BridgeConfigError
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
from anx_bridge.mqtt_bridge import MqttBridge
from anx_bridge.telemetry import build_telemetry


def test_router_known_and_stop():
    r = CommandRouter()
    assert r.execute("forward")
    assert not r.execute("nope")
    r.all_stop()
    assert ("DS", None) in r.calls


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


def test_telemetry_shape():
    body = build_telemetry(control_source="mqtt", deadman_trips=2)
    assert body["control_source"] == "mqtt"
    assert body["deadman_trips_total"] == 2
    assert "ultrasonic_distance_cm" in body


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


def test_bridge_disabled_is_noop():
    from anx_bridge.bridge import start_bridge

    # Must not raise when disabled
    start_bridge(config=BridgeConfig.from_env({"ANX_BRIDGE_ENABLED": "false"}))
