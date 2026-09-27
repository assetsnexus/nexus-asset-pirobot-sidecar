#!/usr/bin/env python3
"""ANX overlay entry for the Adeept Flask web UI.

Loads vendor ``adeept_rasptank2/web/app.py`` without modifying the submodule tree.
Adds unauthenticated ``GET /health`` for compose / Docker healthchecks and
starts the MQTT bridge when ``ANX_BRIDGE_ENABLED`` is true (ipc Mosquitto).
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

_OVERLAY_DIR = Path(__file__).resolve().parent
_WEB_DIR = Path(os.environ.get("ANX_ROBOT_WEB_DIR", "")).resolve() if os.environ.get("ANX_ROBOT_WEB_DIR") else (
    _OVERLAY_DIR.parent / "adeept_rasptank2" / "web"
).resolve()

# Overlay package (anx_bridge) lives next to this entry; vendor web is on path for hardware.
if str(_OVERLAY_DIR) not in sys.path:
    sys.path.insert(0, str(_OVERLAY_DIR))

if not (_WEB_DIR / "app.py").is_file():
    sys.stderr.write(f"anx_web_entry: vendor app not found at {_WEB_DIR / 'app.py'}\n")
    sys.exit(1)

os.chdir(_WEB_DIR)
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

_vendor = None
app = Flask(__name__)
_log = logging.getLogger("anx_web_entry")


def _load_vendor_app():
    """Load upstream app.py. Public f5fe667 imports the Pi camera at import time."""
    global _vendor, app
    spec = importlib.util.spec_from_file_location("adeept_rasptank2_web_app", _WEB_DIR / "app.py")
    if spec is None or spec.loader is None:
        _log.warning("vendor app.py could not be loaded; serving /health only")
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules["adeept_rasptank2_web_app"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        _log.warning("vendor app.py failed to import (%s); serving /health only", exc)
        return
    if not hasattr(module, "app"):
        _log.warning("vendor app.py has no Flask app; serving /health only")
        return
    _vendor = module
    app = module.app


def _install_health() -> None:
    """Attach /health after the vendor app is chosen. Public f5fe667 has no login gate."""
    original = list(app.before_request_funcs.get(None, []))
    app.before_request_funcs[None] = []

    @app.before_request
    def _anx_auth_gate():
        if request.path == "/health" or request.endpoint == "health":
            return None
        for handler in original:
            result = handler()
            if result is not None:
                return result
        return None

    @app.route("/health")
    def health():
        return jsonify({"ok": True, "service": "anx-robot-sidecar"}), 200


def _start_anx_bridge() -> None:
    """Start MQTT bridge from overlay; never patches vendor app.py / webServer."""
    log = logging.getLogger("anx_web_entry")
    try:
        from anx_bridge import start_bridge
        from anx_bridge.hardware import HardwareExecutor, build_sample_fn

        start_bridge(executor=HardwareExecutor(), sample=build_sample_fn())
    except Exception as exc:
        log.warning("ANX bridge not started: %s", exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    _load_vendor_app()
    _install_health()
    _start_anx_bridge()
    # Prefer the vendor webapp bootstrap (camera + Flask thread) when available.
    if hasattr(_vendor, "webapp"):
        web = _vendor.webapp()
        web.startthread()
        # Non-daemon Flask thread keeps the process alive; block main for systemd/docker.
        import threading

        for t in threading.enumerate():
            if t is not threading.current_thread() and t.is_alive():
                t.join()
        return
    port = int(os.environ.get("ROBOT_HTTP_PORT", os.environ.get("PORT", "5000")))
    app.run(host="0.0.0.0", port=port, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("exit")
        raise
