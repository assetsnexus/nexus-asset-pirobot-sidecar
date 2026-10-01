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

from anx_tls import ssl_server_context, tls_enabled, tls_paths

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

# Updated by the control-WS thread; exposed on GET /health for deploy diagnosis.
_control_ws_state: dict = {
    "listening": False,
    "port": int(os.environ.get("ROBOT_WS_PORT", "8888")),
    "scheme": "ws",
    "error": None,
}


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
        tls_on = tls_enabled() and ssl_server_context() is not None
        return jsonify(
            {
                "ok": True,
                "service": "anx-robot-sidecar",
                "tls": tls_on,
                "control_ws": dict(_control_ws_state),
            }
        ), 200


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


def _install_ws_scheme_rewrite() -> None:
    """Stock UI hardcodes ws://host:8888. Make the scheme follow the page protocol.

    On HTTPS pages browsers block ws:// (mixed content). A hardcoded wss:// would
    break plain HTTP. Protocol-relative selection works for both.
    """
    web_dir = Path(os.environ.get("ANX_ROBOT_WEB_DIR", "/app"))
    if not web_dir.is_dir():
        web_dir = _WEB_DIR
    needle_ws = '"ws://"+location.hostname'
    needle_wss = '"wss://"+location.hostname'
    repl = '("https:"===location.protocol?"wss://":"ws://")+location.hostname'
    patched = 0
    for path in web_dir.rglob("*.js"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _log.warning("could not read %s for ws scheme rewrite: %s", path, exc)
            continue
        if needle_ws not in text and needle_wss not in text:
            continue
        text = text.replace(needle_ws, repl).replace(needle_wss, repl)
        try:
            path.write_text(text, encoding="utf-8")
            patched += 1
            _log.info("patched WebSocket scheme to follow location.protocol in %s", path)
        except OSError as exc:
            _log.warning("could not patch %s for ws scheme: %s", path, exc)
    if patched:
        _log.info("patched %s JS file(s) for protocol-aware control WebSocket URL", patched)


def _require_tls_context():
    """When ANX_ROBOT_TLS is on, refuse to start without certs (no silent HTTP fallback)."""
    if not tls_enabled():
        return None
    ctx = ssl_server_context()
    if ctx is None:
        cert = os.environ.get("ANX_ROBOT_TLS_CERT", "/certs/tls/robot.crt")
        key = os.environ.get("ANX_ROBOT_TLS_KEY", "/certs/tls/robot.key")
        raise SystemExit(
            f"ANX_ROBOT_TLS=true but cert/key missing ({cert} / {key}). "
            "Run ./up.sh to generate data/certs/, or set ANX_ROBOT_TLS=false."
        )
    return ctx


def _run_wsgi(flask_app, *, host: str, port: int) -> None:
    """Serve Flask with enough concurrency for HTTPS UI + long-lived /video_feed.

    Werkzeug's ``app.run(ssl_context=...)`` can stall under browser load (MJPEG
    /video_feed holds a worker). Prefer gunicorn gthread — but gunicorn's Arbiter
    installs signal handlers and **must** run on the main thread. Vendor
    ``webapp.startthread()`` runs Flask off-main; we therefore serve from
    ``main()`` instead of that thread.
    """
    import threading

    cert, key = tls_paths() if tls_enabled() else (None, None)
    use_tls = cert is not None and key is not None
    threads = int(os.environ.get("ANX_ROBOT_HTTP_THREADS", "16"))
    on_main = threading.current_thread() is threading.main_thread()

    def _werkzeug() -> None:
        ssl_args = {"ssl_context": ssl_server_context()} if use_tls else {}
        scheme = "https" if use_tls else "http"
        _log.info(
            "UI server %s://%s:%s (werkzeug threaded=%s)",
            scheme,
            host,
            port,
            True,
        )
        flask_app.run(host=host, port=port, threaded=True, **ssl_args)

    if not on_main:
        _log.warning(
            "WSGI started off main thread — gunicorn cannot install signals; "
            "using werkzeug (prefer serving from main())"
        )
        _werkzeug()
        return

    try:
        from gunicorn.app.base import BaseApplication
    except Exception as exc:
        _log.warning(
            "gunicorn unavailable (%s); falling back to werkzeug (may stall under browser load)",
            exc,
        )
        _werkzeug()
        return

    options = {
        "bind": f"{host}:{port}",
        "workers": 1,
        "threads": max(4, threads),
        "worker_class": "gthread",
        # /video_feed is an infinite MJPEG stream — do not kill the worker.
        "timeout": 0,
        "graceful_timeout": 30,
        "keepalive": 5,
        "accesslog": None,
        "errorlog": "-",
        "loglevel": "info",
    }
    if use_tls:
        options["certfile"] = str(cert)
        options["keyfile"] = str(key)

    class _App(BaseApplication):
        def __init__(self, application, cfg):
            self.application = application
            self.cfg_dict = cfg
            super().__init__()

        def load_config(self):
            for key, value in self.cfg_dict.items():
                self.cfg.set(key, value)

        def load(self):
            return self.application

    scheme = "https" if use_tls else "http"
    _log.info(
        "UI server %s://%s:%s (gunicorn gthread workers=1 threads=%s)",
        scheme,
        host,
        port,
        options["threads"],
    )
    _App(flask_app, options).run()


def _http_port() -> int:
    return int(os.environ.get("ROBOT_HTTP_PORT", os.environ.get("PORT", "5000")))


def _vendor_flask_app():
    flask_app = getattr(_vendor, "app", None) if _vendor is not None else None
    return flask_app if flask_app is not None else app


def _prepare_flask_ui() -> None:
    """Validate TLS and log bind target (Flask is served from main, not startthread)."""
    if tls_enabled():
        _require_tls_context()
    _log.info(
        "Flask UI will serve on 0.0.0.0:%s via gunicorn (main thread)",
        _http_port(),
    )



def _start_adeept_control_websocket(flask_webapp) -> None:
    """Run vendor webServer.py control WebSocket on :8888 (UI hardcodes that port).

    Stock Adeept entry is ``python webServer.py``, which starts Flask *and* the
    websocket. Our entry only started Flask, so the camera UI loaded but control
    failed with ``ws://host:8888`` connection errors.
    """
    import threading
    import traceback

    port = int(os.environ.get("ROBOT_WS_PORT", "8888"))
    _control_ws_state["port"] = port
    _control_ws_state["listening"] = False
    _control_ws_state["error"] = None

    if _vendor is None:
        _control_ws_state["error"] = "vendor app not loaded"
        _log.error("control WebSocket not started: vendor app not loaded")
        return

    try:
        import websockets  # noqa: F401 — fail fast before heavy vendor import
    except Exception as exc:
        _control_ws_state["error"] = f"websockets package missing: {exc}"
        _log.error(
            "control WebSocket disabled: websockets not installed (%s). "
            "Rebuild image so requirements-bridge.txt is applied.",
            exc,
        )
        return

    # webServer does ``import app`` — reuse the already-loaded vendor module so
    # Camera()/Flask are not constructed a second time.
    sys.modules.setdefault("app", _vendor)
    try:
        from anx_bridge import release_line_sensors_for_vendor

        release_line_sensors_for_vendor()
    except Exception as exc:
        _log.warning("line GPIO release before webServer import failed: %s", exc)
    try:
        import webServer as ws_mod
    except Exception as exc:
        _control_ws_state["error"] = f"webServer import failed: {exc}"
        _log.error(
            "Adeept webServer.py could not be imported; UI control WS disabled:\n%s",
            traceback.format_exc(),
        )
        return

    try:
        from anx_bridge import adopt_vendor_line_sensors

        adopt_vendor_line_sensors()
    except Exception:
        pass

    ws_mod.flask_app = flask_webapp
    try:
        from anx_bridge.servos import (
            arm_rest_deg,
            register_servo_ctrl,
            release_on_controllers,
        )

        # Vendor scGear.moveInit() drives mid poses and holds torque. Drop PWM
        # immediately so servos stay limp until a control socket connects.
        ctrls = (
            getattr(ws_mod, "scGear", None),
            getattr(ws_mod, "H1_sc", None),
            getattr(ws_mod, "H2_sc", None),
            getattr(ws_mod, "P_sc", None),
            getattr(ws_mod, "T_sc", None),
            getattr(ws_mod, "G_sc", None),
        )
        rest = arm_rest_deg()
        for ctrl in ctrls:
            if ctrl is None:
                continue
            register_servo_ctrl(ctrl)
            if hasattr(ctrl, "initPos") and len(ctrl.initPos) > 0:
                ctrl.initPos[0] = rest
        release_on_controllers(ctrls)
        _log.info(
            "servos released after webServer import (idle until WS connect/control; arm rest init=%s°)",
            rest,
        )
    except Exception as exc:
        _log.warning("servo release after webServer import failed: %s", exc)

    # Overlay-only wrap: stock UI commands count as remote control for idle lights.
    try:
        from anx_bridge import note_ui_control

        if hasattr(ws_mod, "robotCtrl"):
            _orig_robot_ctrl = ws_mod.robotCtrl

            def _robot_ctrl_with_idle(command_input, response):
                try:
                    note_ui_control()
                except Exception:
                    pass
                return _orig_robot_ctrl(command_input, response)

            ws_mod.robotCtrl = _robot_ctrl_with_idle
            _log.info("wrapped webServer.robotCtrl for idle police indicator")
    except Exception as exc:
        _log.warning("could not wrap webServer.robotCtrl for idle lights: %s", exc)

    try:
        ws_mod.switch.switchSetup()
        ws_mod.switch.set_all_switch_off()
    except Exception as exc:
        _log.warning("Adeept switch setup failed: %s", exc)

    try:
        import move

        move.setup()
    except Exception as exc:
        _log.warning("Adeept move.setup() failed (WS will still listen): %s", exc)

    ssl_ctx = ssl_server_context()
    _control_ws_state["scheme"] = "wss" if ssl_ctx is not None else "ws"
    _control_ws_state["clients"] = 0

    def _run() -> None:
        import asyncio

        import websockets

        clients = {"n": 0}

        def _set_clients(n: int) -> None:
            clients["n"] = n
            _control_ws_state["clients"] = n
            try:
                from anx_bridge import set_control_socket_clients

                set_control_socket_clients(n)
            except Exception as exc:
                _log.debug("status lights ws client update failed: %s", exc)

        async def handler(websocket):
            _set_clients(clients["n"] + 1)
            try:
                # websockets>=10 dropped the path argument; vendor main_logic still has it.
                await ws_mod.check_permit(websocket)
                await ws_mod.recv_msg(websocket)
            finally:
                _set_clients(max(0, clients["n"] - 1))

        async def runner() -> None:
            serve_kwargs = {"ssl": ssl_ctx} if ssl_ctx is not None else {}
            scheme = "wss" if ssl_ctx is not None else "ws"
            # Stock webServer retries bind forever; keep a few attempts for docker races.
            last_exc: Exception | None = None
            for attempt in range(1, 6):
                try:
                    async with websockets.serve(handler, "0.0.0.0", port, **serve_kwargs):
                        _control_ws_state["listening"] = True
                        _control_ws_state["error"] = None
                        _log.info(
                            "Adeept control WebSocket listening on %s://0.0.0.0:%s "
                            "(UI login admin:123456)",
                            scheme,
                            port,
                        )
                        await asyncio.Future()
                    return
                except OSError as exc:
                    last_exc = exc
                    _log.warning(
                        "control WebSocket bind attempt %s/5 failed: %s", attempt, exc
                    )
                    await asyncio.sleep(1.0)
            _control_ws_state["listening"] = False
            _control_ws_state["error"] = f"bind failed: {last_exc}"
            _log.error("Adeept control WebSocket could not bind :%s (%s)", port, last_exc)

        try:
            asyncio.run(runner())
        except Exception as exc:
            _control_ws_state["listening"] = False
            _control_ws_state["error"] = str(exc)
            _log.error(
                "Adeept control WebSocket exited:\n%s", traceback.format_exc()
            )

    threading.Thread(target=_run, name="adeept-control-ws", daemon=True).start()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Patch Camera.frames before app.py does `camera = Camera()` (starts the thread).
    _install_camera_frames_guard()
    _load_vendor_app()
    _install_static_fallback()
    _install_health()
    _install_ws_scheme_rewrite()
    _start_anx_bridge()
    # Prefer the vendor webapp bootstrap (camera object) when available.
    # Serve Flask on the **main** thread so gunicorn can install signal handlers
    # (vendor startthread() would put WSGI off-main and crash gunicorn).
    if _vendor is not None and hasattr(_vendor, "webapp"):
        web = _vendor.webapp()
        _prepare_flask_ui()
        _start_adeept_control_websocket(web)
        _run_wsgi(_vendor_flask_app(), host="0.0.0.0", port=_http_port())
        return
    port = _http_port()
    _start_adeept_control_websocket(app)
    if tls_enabled():
        _require_tls_context()
    _run_wsgi(app, host="0.0.0.0", port=port)



if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("exit")
        raise
