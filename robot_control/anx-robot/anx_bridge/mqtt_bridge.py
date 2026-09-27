"""MQTT subscribe/publish for robot control and telemetry.

Hardware-free: the paho client is injected so tests do not need a broker.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from .command_router import CommandRouter
from .config import BridgeConfig

logger = logging.getLogger(__name__)


class MqttBridge:
    def __init__(
        self,
        config: BridgeConfig,
        router: CommandRouter,
        publish: Optional[Callable[[str, str, bool], None]] = None,
        on_controller: Optional[Callable[[dict], None]] = None,
    ):
        self.config = config
        self.router = router
        self._publish = publish or (lambda topic, payload, retain: None)
        self._on_controller = on_controller
        self.subscriptions = [
            f"{config.topic_prefix}/cmd",
            f"{config.topic_prefix}/controller/+",
        ]

    def on_message(self, topic: str, payload: str) -> None:
        prefix = self.config.topic_prefix
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            logger.warning("invalid mqtt json on %s", topic)
            return
        if topic == f"{prefix}/cmd" or topic.endswith("/cmd"):
            action = data.get("action")
            if action == "allStop":
                self.router.all_stop()
                return
            if isinstance(action, str):
                self.router.execute(action, data.get("value"))
            return
        if "/controller/" in topic and isinstance(data, dict):
            if self._on_controller is not None:
                self._on_controller(data)
            return

    def publish_telemetry(self, body: dict[str, Any]) -> None:
        self._publish(f"{self.config.topic_prefix}/telemetry", json.dumps(body), False)

    def publish_state(self, body: dict[str, Any]) -> None:
        self._publish(f"{self.config.topic_prefix}/state", json.dumps(body), True)

    def publish_availability(self, online: bool) -> None:
        self._publish(
            f"{self.config.topic_prefix}/availability",
            "online" if online else "offline",
            True,
        )
