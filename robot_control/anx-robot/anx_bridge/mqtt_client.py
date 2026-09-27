"""Paho MQTT session. TLS is the default; plaintext requires ANX_MQTT_INSECURE.

Joins the ipc-docker Mosquitto (MQTT_CA_FILE). Does not generate a local CA or broker.
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import Callable, Optional

from .config import BridgeConfig

logger = logging.getLogger(__name__)

OnMessage = Callable[[str, str], None]


def _new_paho(client_id: str):
    import paho.mqtt.client as mqtt

    kwargs = {"client_id": client_id}
    version = getattr(mqtt, "CallbackAPIVersion", None)
    if version is not None:
        kwargs["callback_api_version"] = version.VERSION1
    return mqtt.Client(**kwargs)


class PahoSession:
    def __init__(self, config: BridgeConfig, on_message: OnMessage, client=None):
        self.config = config
        self._on_message = on_message
        self.client = client if client is not None else _new_paho(f"anx-robot-{config.asset_id}")
        self._wire()

    def _wire(self) -> None:
        client = self.client
        if self.config.mqtt_tls:
            ca = self.config.mqtt_ca
            if not os.path.isfile(ca):
                raise FileNotFoundError(
                    f"MQTT CA not found at {ca}. Run asset-node-ipc-docker ./prepare.sh "
                    "(oem profile) first so data/mqtt/certs/ca.crt exists, then mount it as MQTT_CA_FILE."
                )
            client.tls_set(ca_certs=ca, tls_version=ssl.PROTOCOL_TLS_CLIENT)
            client.tls_insecure_set(False)
        if self.config.mqtt_user:
            client.username_pw_set(self.config.mqtt_user, self.config.mqtt_password)
        client.will_set(f"{self.config.topic_prefix}/availability", "offline", retain=True)
        if hasattr(client, "reconnect_delay_set"):
            client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_message = self._handle

    def _handle(self, _client, _userdata, message) -> None:
        payload = message.payload.decode("utf-8", errors="replace") if message.payload else ""
        self._on_message(message.topic, payload)

    def start(self) -> None:
        for topic in (
            f"{self.config.topic_prefix}/cmd",
            f"{self.config.topic_prefix}/controller/+",
        ):
            self.client.subscribe(topic)
        logger.info(
            "MQTT connecting %s:%s tls=%s",
            self.config.mqtt_host,
            self.config.mqtt_port,
            self.config.mqtt_tls,
        )
        self.client.connect_async(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
        self.client.loop_start()

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self.client.publish(topic, payload, qos=1, retain=retain)

    def stop(self) -> None:
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as exc:
            logger.debug("mqtt stop: %s", exc)
