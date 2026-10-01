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
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

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


def _placeholder_jpeg() -> bytes:
    """Black 640x480 JPEG so /video_feed stays alive when libcamera sees no sensor."""
    try:
        import cv2
        import numpy as np

        img = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(
            img,
            "NO CAMERA (map /dev/video* + /run/udev)",
            (40, 240),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        ok, buf = cv2.imencode(".jpg", img)
        if ok:
            return buf.tobytes()
    except Exception as exc:
        _log.warning("placeholder frame encode failed: %s", exc)
    # Minimal JPEG (1x1 pixel) if OpenCV is unavailable.
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
        b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\xff\xd9"
    )


def _placeholder_frames():
    jpeg = _placeholder_jpeg()
    while True:
        yield jpeg
        time.sleep(0.05)


def _install_camera_frames_guard() -> None:
    """Wrap vendor Camera.frames before app.py constructs Camera().

    Vendor Picamera2() raises IndexError when libcamera's camera list is empty
    (typical if /dev/video* or /run/udev were not passed into the container).
    """
    try:
        import camera_opencv as cov
    except Exception as exc:
        _log.warning("camera_opencv unavailable for guard (%s)", exc)
        return

    real_frames = getattr(cov.Camera.__dict__.get("frames"), "__func__", None) or cov.Camera.frames

    @staticmethod
    def frames():
        try:
            from picamera2 import Picamera2

            infos = Picamera2.global_camera_info()
        except Exception as exc:
            _log.error("libcamera probe failed (%s); serving placeholder video", exc)
            yield from _placeholder_frames()
            return
        if not infos:
            _log.error(
                "libcamera sees 0 cameras — map /dev/video* /dev/media* /dev/dma_heap* "
                "and mount /run/udev (re-run ./up.sh). Serving placeholder video."
            )
            yield from _placeholder_frames()
            return
        _log.info("libcamera cameras: %s", infos)
        yield from real_frames()

    cov.Camera.frames = frames


def _load_vendor_app():
    """Load upstream app.py. Public f5fe667 imports the Pi camera at import time."""
    global _vendor, app
    spec = importlib.util.spec_from_file_location("adeept_rasptank2_web_app", _WEB_DIR / "app.py")
    if spec is None or spec.loader is None:
        _log.warning("vendor app.py could not be loaded; serving static UI + /health")
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules["adeept_rasptank2_web_app"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        _log.warning("vendor app.py failed to import (%s); serving static UI + /health", exc)
        return
    if not hasattr(module, "app"):
        _log.warning("vendor app.py has no Flask app; serving static UI + /health")
        return
    _vendor = module
    app = module.app


def _install_static_fallback() -> None:
    """Serve Adeept dist/ when the camera stack failed to import (no blank 404 on /)."""
    if _vendor is not None:
        return
    dist = _WEB_DIR / "dist"
    if not dist.is_dir():
        _log.warning("vendor dist/ missing at %s; only /health will answer", dist)
        return

    @app.route("/")
    def index():
        return send_from_directory(dist, "index.html")

    @app.route("/js/<path:filename>")
    def sendjs(filename):
        return send_from_directory(dist / "js", filename)

    @app.route("/css/<path:filename>")
    def sendcss(filename):
        return send_from_directory(dist / "css", filename)

    @app.route("/fonts/<path:filename>")
    def sendfonts(filename):
        return send_from_directory(dist / "fonts", filename)

    @app.route("/api/img/<path:filename>")
    def sendimg(filename):
        return send_from_directory(dist / "img", filename)

    @app.route("/api/img/icon/<path:filename>")
    def sendicon(filename):
        return send_from_directory(dist / "img" / "icon", filename)

    @app.route("/<path:filename>")
    def sendgen(filename):
        return send_from_directory(dist, filename)

    _log.info("static Adeept UI mounted from %s (camera/vendor import unavailable)", dist)


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
        from anx_bridge.config import BridgeConfig
        from anx_bridge.hardware import HardwareExecutor, build_sample_fn

        cfg = BridgeConfig.from_env()
        executor = HardwareExecutor(motion_config=cfg.motion)
        start_bridge(config=cfg, executor=executor, sample=build_sample_fn(executor))
    except Exception as exc:
        log.warning("ANX bridge not started: %s", exc)


def _start_adeept_control_websocket(flask_webapp) -> None:
    """Run vendor webServer.py control WebSocket on :8888 (UI hardcodes that port).

    Stock Adeept entry is ``python webServer.py``, which starts Flask *and* the
    websocket. Our entry only started Flask, so the camera UI loaded but control
    failed with ``ws://host:8888`` connection errors.
    """
    if _vendor is None:
        return
    # webServer does ``import app`` — reuse the already-loaded vendor module so
    # Camera()/Flask are not constructed a second time.
    sys.modules.setdefault("app", _vendor)
    try:
        import webServer as ws_mod
    except Exception as exc:
        _log.warning("Adeept webServer.py could not be imported (%s); UI control WS disabled", exc)
        return

    ws_mod.flask_app = flask_webapp
    try:
        ws_mod.switch.switchSetup()
        ws_mod.switch.set_all_switch_off()
    except Exception as exc:
        _log.warning("Adeept switch setup failed: %s", exc)

    port = int(os.environ.get("ROBOT_WS_PORT", "8888"))

    def _run() -> None:
        import asyncio

        try:
            import websockets
        except Exception as exc:
            _log.error("websockets package missing (%s); pip install websockets==13.0", exc)
            return

        async def handler(websocket):
            # websockets>=10 dropped the path argument; vendor still declares it.
            await ws_mod.check_permit(websocket)
            await ws_mod.recv_msg(websocket)

        async def runner() -> None:
            async with websockets.serve(handler, "0.0.0.0", port):
                _log.info("Adeept control WebSocket listening on 0.0.0.0:%s (UI login admin:123456)", port)
                await asyncio.Future()

        try:
            asyncio.run(runner())
        except Exception as exc:
            _log.error("Adeept control WebSocket exited: %s", exc)

    import threading

    threading.Thread(target=_run, name="adeept-control-ws", daemon=True).start()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Patch Camera.frames before app.py does `camera = Camera()` (starts the thread).
    _install_camera_frames_guard()
    _load_vendor_app()
    _install_static_fallback()
    _install_health()
    _start_anx_bridge()
    # Prefer the vendor webapp bootstrap (camera + Flask thread) when available.
    if hasattr(_vendor, "webapp"):
        web = _vendor.webapp()
        _start_adeept_control_websocket(web)
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
