"""Start/stop the ANX bridge without taking over the Flask camera."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .command_router import CommandRouter
from .config import BridgeConfig
from .controller_input import (
    apply_controller_message,
    list_gamepads,
    load_map,
    select_source,
    start_usb_reader,
)
from .deadman import Deadman
from .hardware import HardwareExecutor
from .idle_police import StatusLightsController
from .mqtt_bridge import MqttBridge
from .mqtt_client import PahoSession
from .servos import ensure_servos_armed, release_servos
from .telemetry import build_telemetry

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_session: Optional[PahoSession] = None
_deadman: Optional[Deadman] = None
_status_lights: Optional[StatusLightsController] = None
_executor_ref: Optional[HardwareExecutor] = None

# Compat alias for older call sites.
_idle_police = None

SampleFn = Callable[[str, int], dict]


def poke_node_heartbeat() -> None:
    """Public API: asset-node (or tests) mark the control path as alive."""
    if _deadman is not None:
        _deadman.poke_node_heartbeat()


def note_ui_control(*, arm_servos: bool = True) -> None:
    """Stock Adeept UI drove a command — optionally enable PWM + clear idle lights.

    Never auto-parks the shoulder: holding an endstop pose overheats the servo.
    """
    if arm_servos:
        ensure_servos_armed(park_arm=False)
    if _status_lights is not None:
        _status_lights.note_control()


def set_control_socket_clients(count: int) -> None:
    """UI control WebSocket client count.

    Connected → blue status only (servos stay limp until a real command).
    Disconnected → release PWM (limp) + red status.
    """
    connected = int(count) > 0
    if _status_lights is not None:
        _status_lights.set_ws_connected(connected)
    if not connected:
        release_servos()


def release_line_sensors_for_vendor() -> None:
    """Drop overlay IR line claims so vendor ``functions.setup()`` can open GPIO17/27/22."""
    if _executor_ref is not None:
        try:
            _executor_ref.sensors.release_line()
            logger.info("released overlay line IR GPIOs for vendor webServer import")
        except Exception as exc:
            logger.warning("could not release line sensors: %s", exc)


def adopt_vendor_line_sensors() -> None:
    if _executor_ref is not None:
        try:
            _executor_ref.sensors.adopt_vendor_line_sensors()
        except Exception as exc:
            logger.debug("adopt vendor line sensors: %s", exc)


def start_bridge(
    config: Optional[BridgeConfig] = None,
    executor: Optional[Callable] = None,
    sample: Optional[SampleFn] = None,
) -> None:
    global _thread, _session, _deadman, _status_lights, _idle_police, _executor_ref
    cfg = config or BridgeConfig.from_env()
    if not cfg.enabled:
        logger.info("ANX bridge disabled (ANX_BRIDGE_ENABLED)")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    if isinstance(executor, HardwareExecutor):
        # Apply overlay motion defaults from bridge config when executor used defaults.
        executor._motion_cfg = cfg.motion  # noqa: SLF001 — wire config before first motion
        _executor_ref = executor
        if sample is None:
            from .hardware import build_sample_fn

            sample = build_sample_fn(executor)

    router = CommandRouter(executor)
    deadman = Deadman(
        router,
        cfg.deadman_ms,
        node_heartbeat_timeout_ms=cfg.motion.node_heartbeat_timeout_ms,
    )
    _deadman = deadman

    status_lights = None
    if isinstance(executor, HardwareExecutor) and cfg.idle_police_enabled:
        status_lights = StatusLightsController(
            executor,
            idle_ms=cfg.idle_police_ms,
            enabled=True,
        )
        _status_lights = status_lights
        _idle_police = status_lights
        # Start disconnected → red until a UI control socket connects.
        status_lights.set_ws_connected(False)
        logger.info(
            "status lights enabled (red=disconnected, blue=connected; idle >= %sms)",
            cfg.idle_police_ms,
        )
    else:
        _status_lights = None
        _idle_police = None

    published_holder: dict = {}

    def publish_fast(body: dict) -> None:
        bridge = published_holder.get("mqtt")
        if bridge is not None:
            bridge.publish_telemetry_fast(body)

    if isinstance(executor, HardwareExecutor):
        executor.attach_runtime(
            deadman=deadman, publish_fast=publish_fast, idle_police=status_lights
        )

    try:
        mapping = load_map(cfg.controller_map_path)
    except OSError as exc:
        logger.warning("controller map not loaded: %s", exc)
        mapping = {}

    def on_controller(data: dict) -> None:
        if cfg.control_source == "usb":
            return
        apply_controller_message(mapping, data, router, deadman)

    holder: dict = published_holder

    def on_message(topic: str, payload: str) -> None:
        bridge = holder.get("mqtt")
        if bridge is None:
            return
        if topic.endswith("/cmd"):
            # Immediate RC / invoke commands refresh MQTT quiet deadman.
            # Timed motions refresh via set_timed_motion_active instead.
            action = None
            try:
                import json

                action = (json.loads(payload) or {}).get("action")
            except Exception:
                action = None
            if action not in (
                "drive_cm",
                "drive_sequence",
                "turn_left_90",
                "turn_right_90",
            ):
                deadman.poke()
        bridge.on_message(topic, payload)

    session = PahoSession(cfg, on_message)
    mqtt = MqttBridge(
        cfg,
        router,
        publish=session.publish,
        on_controller=on_controller,
        on_node_heartbeat=deadman.poke_node_heartbeat,
    )
    holder["mqtt"] = mqtt
    _session = session

    source = select_source(list_gamepads(), cfg.control_source)
    logger.info("control source %s (requested %s)", source, cfg.control_source)
    if source == "usb":
        start_usb_reader(mapping, router, deadman, _stop)

    def loop() -> None:
        logger.info(
            "ANX bridge started prefix=%s tls=%s host=%s",
            cfg.topic_prefix,
            cfg.mqtt_tls,
            cfg.mqtt_host,
        )
        session.start()
        mqtt.publish_availability(True)
        last_full = 0.0
        while not _stop.wait(0.05):
            deadman.tick()
            if status_lights is not None:
                status_lights.tick()
            now = time.monotonic()
            # Fast range publish (~20 Hz) so asset-node guards can see mm promptly.
            if isinstance(executor, HardwareExecutor):
                try:
                    mm = executor.sensors.ultrasonic_mm()
                    mqtt.publish_telemetry_fast({"ultrasonic_mm": mm, "distance_mm": mm})
                except Exception:
                    pass
            if now - last_full >= 1.0:
                last_full = now
                if sample is not None:
                    body = sample(source, deadman.trips)
                else:
                    body = build_telemetry(control_source=source, deadman_trips=deadman.trips)
                mqtt.publish_telemetry(body)
        mqtt.publish_availability(False)
        session.stop()
        logger.info("ANX bridge stopped")

    _thread = threading.Thread(target=loop, name="anx-bridge", daemon=True)
    _thread.start()


def stop_bridge() -> None:
    global _deadman, _status_lights, _idle_police, _executor_ref
    _stop.set()
    _deadman = None
    _status_lights = None
    _idle_police = None
    _executor_ref = None
