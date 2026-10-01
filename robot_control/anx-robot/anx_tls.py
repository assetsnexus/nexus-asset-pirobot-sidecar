"""TLS helpers for the robot sidecar Flask UI and Adeept control WebSocket."""
from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

_log = logging.getLogger("anx_web_entry.tls")


def tls_enabled() -> bool:
    raw = os.environ.get("ANX_ROBOT_TLS", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def tls_paths() -> tuple[Path, Path] | tuple[None, None]:
    cert = Path(os.environ.get("ANX_ROBOT_TLS_CERT", "/certs/tls/robot.crt"))
    key = Path(os.environ.get("ANX_ROBOT_TLS_KEY", "/certs/tls/robot.key"))
    if cert.is_file() and key.is_file():
        return cert, key
    return None, None


def ssl_server_context() -> ssl.SSLContext | None:
    if not tls_enabled():
        return None
    cert, key = tls_paths()
    if not cert or not key:
        _log.warning("ANX_ROBOT_TLS is on but cert/key missing (%s / %s)", cert, key)
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(str(cert), str(key))
    return ctx
