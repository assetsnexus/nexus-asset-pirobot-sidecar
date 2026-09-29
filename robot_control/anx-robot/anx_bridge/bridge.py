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
from .mqtt_bridge import MqttBridge
from .mqtt_client import PahoSession
from .telemetry import build_telemetry

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_session: Optional[PahoSession] = None
_deadman: Optional[Deadman] = None

SampleFn = Callable[[str, int], dict]


def poke_node_heartbeat() -> None:
    """Public API: asset-node (or tests) mark the control path as alive."""
    if _deadman is not None:
        _deadman.poke_node_heartbeat()


def start_bridge(
    config: Optional[BridgeConfig] = None,
    executor: Optional[Callable] = None,
    sample: Optional[SampleFn] = None,
) -> None:
    global _thread, _session, _deadman
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

    published_holder: dict = {}

    def publish_fast(body: dict) -> None:
        bridge = published_holder.get("mqtt")
        if bridge is not None:
            bridge.publish_telemetry_fast(body)

    if isinstance(executor, HardwareExecutor):
        executor.attach_runtime(deadman=deadman, publish_fast=publish_fast)

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
    global _deadman
    _stop.set()
    _deadman = None
