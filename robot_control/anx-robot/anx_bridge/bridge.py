"""Start/stop the ANX bridge without taking over the Flask camera."""

from __future__ import annotations

import logging
import threading
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
from .mqtt_bridge import MqttBridge
from .mqtt_client import PahoSession
from .telemetry import build_telemetry

logger = logging.getLogger(__name__)

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_session: Optional[PahoSession] = None

SampleFn = Callable[[str, int], dict]


def start_bridge(
    config: Optional[BridgeConfig] = None,
    executor: Optional[Callable] = None,
    sample: Optional[SampleFn] = None,
) -> None:
    global _thread, _session
    cfg = config or BridgeConfig.from_env()
    if not cfg.enabled:
        logger.info("ANX bridge disabled (ANX_BRIDGE_ENABLED)")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    router = CommandRouter(executor)
    deadman = Deadman(router, cfg.deadman_ms)
    try:
        mapping = load_map(cfg.controller_map_path)
    except OSError as exc:
        logger.warning("controller map not loaded: %s", exc)
        mapping = {}

    def on_controller(data: dict) -> None:
        if cfg.control_source == "usb":
            return
        apply_controller_message(mapping, data, router, deadman)

    holder: dict = {}

    def on_message(topic: str, payload: str) -> None:
        bridge = holder.get("mqtt")
        if bridge is None:
            return
        if topic.endswith("/cmd"):
            deadman.poke()
        bridge.on_message(topic, payload)

    session = PahoSession(cfg, on_message)
    mqtt = MqttBridge(cfg, router, publish=session.publish, on_controller=on_controller)
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
        while not _stop.wait(1.0):
            deadman.tick()
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
    _stop.set()
