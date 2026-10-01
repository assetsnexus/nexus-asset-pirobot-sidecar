"""Environment configuration for the ANX robot bridge.

Prefers ipc-docker env names (MQTT_BROKER / MQTT_USER / MQTT_PASSWORD / MQTT_CA_FILE)
with fallback to the Pi-local ANX_MQTT_* aliases.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .odometry import (
    DEFAULT_SPEED_AT_FULL_PWM_MPS,
    DEFAULT_TRACK_WIDTH_M,
    DEFAULT_WHEEL_DIAMETER_M,
)


class BridgeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class OverlayMotionConfig:
    """Open-loop motion constants — overlay only, never written into the vendor tree."""

    wheel_diameter_m: float = DEFAULT_WHEEL_DIAMETER_M
    track_width_m: float = DEFAULT_TRACK_WIDTH_M
    speed_at_full_pwm_mps: float = DEFAULT_SPEED_AT_FULL_PWM_MPS
    obstacle_stop_mm: float = 100.0
    node_heartbeat_timeout_ms: int = 2000


@dataclass(frozen=True)
class BridgeConfig:
    enabled: bool
    mqtt_url: str
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool
    mqtt_ca: str
    mqtt_user: str
    mqtt_password: str
    asset_id: str
    topic_prefix: str
    control_source: str  # auto | usb | mqtt
    controller_map_path: str
    deadman_ms: int
    idle_police_enabled: bool
    idle_police_ms: int
    motion: OverlayMotionConfig

    @classmethod
    def from_env(cls, environ: dict | None = None) -> "BridgeConfig":
        env = environ if environ is not None else os.environ
        enabled = env.get("ANX_BRIDGE_ENABLED", "false").strip().lower() in ("1", "true", "yes")
        asset_id = env.get("ANX_ASSET_ID", "local").strip() or "local"
        prefix = env.get("ANX_TOPIC_PREFIX", "").strip() or f"anx/asset/{asset_id}"
        source = env.get("ANX_CONTROL_SOURCE", "auto").strip().lower()
        if source not in ("auto", "usb", "mqtt"):
            raise BridgeConfigError(f"ANX_CONTROL_SOURCE must be auto|usb|mqtt, got {source}")
        try:
            deadman = int(env.get("ANX_DEADMAN_MS", "500"))
        except ValueError as exc:
            raise BridgeConfigError("ANX_DEADMAN_MS must be an integer") from exc
        if deadman < 50:
            raise BridgeConfigError("ANX_DEADMAN_MS must be >= 50")
        idle_police_enabled = env.get("ANX_IDLE_POLICE", "true").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        try:
            idle_police_ms = int(env.get("ANX_IDLE_POLICE_MS", "3000"))
        except ValueError as exc:
            raise BridgeConfigError("ANX_IDLE_POLICE_MS must be an integer") from exc
        if idle_police_ms < 200:
            raise BridgeConfigError("ANX_IDLE_POLICE_MS must be >= 200")
        motion = OverlayMotionConfig(
            wheel_diameter_m=_float_env(env, "ANX_WHEEL_DIAMETER_M", DEFAULT_WHEEL_DIAMETER_M),
            track_width_m=_float_env(env, "ANX_TRACK_WIDTH_M", DEFAULT_TRACK_WIDTH_M),
            speed_at_full_pwm_mps=_float_env(
                env, "ANX_SPEED_AT_FULL_PWM_MPS", DEFAULT_SPEED_AT_FULL_PWM_MPS
            ),
            obstacle_stop_mm=_float_env(env, "ANX_OBSTACLE_STOP_MM", 100.0),
            node_heartbeat_timeout_ms=int(
                float(env.get("ANX_NODE_HEARTBEAT_MS", "2000") or "2000")
            ),
        )
        insecure = env.get("ANX_MQTT_INSECURE", "").strip().lower() in ("1", "true", "yes")
        # Prefer ipc-docker names; fall back to Pi ANX_MQTT_* aliases.
        mqtt_url = (
            env.get("MQTT_BROKER", "").strip()
            or env.get("ANX_MQTT_URL", "").strip()
            or ("mqtts://127.0.0.1:8883" if not insecure else "mqtt://127.0.0.1:1883")
        )
        scheme, host, port = parse_mqtt_url(mqtt_url)
        if enabled and scheme == "mqtt" and not insecure:
            raise BridgeConfigError(
                "MQTT_BROKER/ANX_MQTT_URL is plaintext. Use mqtts:// (default) or set ANX_MQTT_INSECURE=true"
            )
        here = os.path.dirname(os.path.abspath(__file__))
        default_map = os.path.join(here, "controller_map.xbox.json")
        mqtt_ca = (
            env.get("MQTT_CA_FILE", "").strip()
            or env.get("ANX_MQTT_CA", "").strip()
            or "/certs/ca.crt"
        )
        mqtt_user = env.get("MQTT_USER", "").strip() or env.get("ANX_MQTT_USER", "").strip()
        mqtt_password = (
            env.get("MQTT_PASSWORD", "").strip() or env.get("ANX_MQTT_PASSWORD", "").strip()
        )
        return cls(
            enabled=enabled,
            mqtt_url=mqtt_url,
            mqtt_host=host,
            mqtt_port=port,
            mqtt_tls=scheme == "mqtts",
            mqtt_ca=mqtt_ca,
            mqtt_user=mqtt_user,
            mqtt_password=mqtt_password,
            asset_id=asset_id,
            topic_prefix=prefix.rstrip("/"),
            control_source=source,
            controller_map_path=env.get("ANX_CONTROLLER_MAP", default_map).strip() or default_map,
            deadman_ms=deadman,
            idle_police_enabled=idle_police_enabled,
            idle_police_ms=idle_police_ms,
            motion=motion,
        )


def _float_env(env: dict, key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise BridgeConfigError(f"{key} must be a number") from exc


def parse_mqtt_url(url: str) -> tuple[str, str, int]:
    raw = (url or "").strip()
    if raw.startswith("mqtts://"):
        scheme, rest = "mqtts", raw[len("mqtts://") :]
        default_port = 8883
    elif raw.startswith("mqtt://"):
        scheme, rest = "mqtt", raw[len("mqtt://") :]
        default_port = 1883
    else:
        raise BridgeConfigError(
            f"MQTT_BROKER/ANX_MQTT_URL must start with mqtts:// or mqtt://, got {raw!r}"
        )
    if "/" in rest:
        rest = rest.split("/", 1)[0]
    if not rest:
        raise BridgeConfigError("MQTT_BROKER/ANX_MQTT_URL is missing a host")
    if ":" in rest:
        host, port_s = rest.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError as exc:
            raise BridgeConfigError(f"MQTT_BROKER has a bad port: {port_s}") from exc
    else:
        host, port = rest, default_port
    return scheme, host, port
