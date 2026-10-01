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
        path = request.path or ""
        if path == "/health" or request.endpoint == "health" or path.startswith("/anx/"):
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

    _install_servo_api(app)


def _install_servo_api(flask_app) -> None:
    """Absolute servo angles + power estimate APIs for the stock UI overlays."""
    from anx_bridge.power_sense import power_payload, start_power_sampler
    from anx_bridge.servo_positions import describe_servos, set_many, set_servo_angle

    static_dir = _OVERLAY_DIR / "static"
    try:
        start_power_sampler()
    except Exception as exc:
        _log.warning("power sampler not started: %s", exc)

    @flask_app.route("/anx/servos", methods=["GET"])
    def anx_servos_get():
        return jsonify({"ok": True, "servos": describe_servos()}), 200

    @flask_app.route("/anx/servos", methods=["POST"])
    def anx_servos_post():
        try:
            from anx_bridge import note_ui_control

            note_ui_control(arm_servos=False)
        except Exception:
            pass
        body = request.get_json(silent=True) or {}
        try:
            if "positions" in body and isinstance(body["positions"], dict):
                applied = set_many({int(k): int(v) for k, v in body["positions"].items()})
                return jsonify({"ok": True, "positions": applied, "servos": describe_servos()}), 200
            if "channel" not in body or "deg" not in body:
                return jsonify({"ok": False, "error": "need channel+deg or positions"}), 400
            deg = set_servo_angle(int(body["channel"]), int(body["deg"]))
            return jsonify({"ok": True, "channel": int(body["channel"]), "deg": deg}), 200
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            _log.exception("anx/servos set failed")
            return jsonify({"ok": False, "error": str(exc)}), 500

    @flask_app.route("/anx/power", methods=["GET"])
    def anx_power_get():
        return jsonify(power_payload()), 200

    def _js(name: str):
        path = static_dir / name
        if not path.is_file():
            return f"/* {name} missing */", 404, {"Content-Type": "application/javascript"}
        return path.read_text(encoding="utf-8"), 200, {
            "Content-Type": "application/javascript; charset=utf-8",
            "Cache-Control": "no-store",
        }

    @flask_app.route("/anx/arm_sliders.js")
    def anx_arm_sliders_js():
        return _js("arm_sliders.js")

    @flask_app.route("/anx/power_badge.js")
    def anx_power_badge_js():
        return _js("power_badge.js")

    _log.info("servo + power APIs mounted (/anx/servos, /anx/power, static overlays)")


def _install_get_info_battery(ws_mod) -> None:
    """Inject estimated load watts into get_info (Hard Ware chip under CPU Usage).

    Watts ≈ V * (V_rest - V) / R_esr from battery sag — HAT has no current shunt.
    """
    import json

    from anx_bridge.power_sense import last_sample, sample_power

    if not hasattr(ws_mod, "recv_msg"):
        return
    _orig_recv_msg = ws_mod.recv_msg

    async def recv_msg_with_power(websocket):
        _send = websocket.send

        async def send_wrap(payload):
            try:
                obj = json.loads(payload) if isinstance(payload, str) else payload
            except Exception:
                return await _send(payload)
            if isinstance(obj, dict) and obj.get("title") == "get_info":
                data = obj.get("data")
                if isinstance(data, list) and len(data) == 3:
                    sample = last_sample() or sample_power()
                    watts = "" if sample is None else str(sample.power_w_est)
                    obj["data"] = [data[0], data[1], watts, data[2]]
                    payload = json.dumps(obj)
            return await _send(payload)

        websocket.send = send_wrap  # type: ignore[method-assign]
        try:
            await _orig_recv_msg(websocket)
        finally:
            websocket.send = _send  # type: ignore[method-assign]

    ws_mod.recv_msg = recv_msg_with_power
    _log.info("wrapped webServer.recv_msg to inject estimated watts into get_info")


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


def _install_ui_https_rewrites() -> None:
    """Fix stock UI URLs that hardcode http:// / ws:// (mixed content on HTTPS + IP hosts).

    Browsers will not auto-upgrade http→https when the host is an IP, so the
    hardcoded ``http://hostname:5000/video_feed`` fails under HTTPS with
    ERR_EMPTY_RESPONSE / Mixed Content. Same for ``ws://`` control sockets.
    """
    web_dir = Path(os.environ.get("ANX_ROBOT_WEB_DIR", "/app"))
    if not web_dir.is_dir():
        web_dir = _WEB_DIR

    replacements = (
        (
            '"ws://"+location.hostname',
            '("https:"===location.protocol?"wss://":"ws://")+location.hostname',
        ),
        (
            '"wss://"+location.hostname',
            '("https:"===location.protocol?"wss://":"ws://")+location.hostname',
        ),
        # Prefer same-origin so scheme+port follow the page (fixes IP mixed content).
        (
            'o.src="http://"+location.hostname+":5000/video_feed?rand="+t.rand,o.onload=function(){n.drawImage(o,0,0,640,480)}',
            'o.__anxT0=performance.now(),o.src=location.origin+"/video_feed?rand="+t.rand,o.onload=function(){window.__anxVideoMs=Math.round(performance.now()-o.__anxT0);n.drawImage(o,0,0,640,480)}',
        ),
        (
            '"http://"+location.hostname+":5000/video_feed',
            'location.origin+"/video_feed',
        ),
        # Status chips: Load Power (est. W from battery sag) under CPU Usage.
        (
            'chips:[["CPU","Temp",50,"°C",55,70],["CPU","Usage",75,"%",70,85],["RAM","Usage",90,"%",70,85]]',
            'chips:[["CPU","Temp",50,"°C",55,70],["CPU","Usage",75,"%",70,85],["Load","Power",0,"W",6,12],["RAM","Usage",90,"%",70,85]]',
        ),
        (
            'chips:[["CPU","Temp",50,"°C",55,70],["CPU","Usage",75,"%",70,85],["Batt","Volt",0,"V",6.4,7.2],["RAM","Usage",90,"%",70,85]]',
            'chips:[["CPU","Temp",50,"°C",55,70],["CPU","Usage",75,"%",70,85],["Load","Power",0,"W",6,12],["RAM","Usage",90,"%",70,85]]',
        ),
    )

    patched = 0
    for path in web_dir.rglob("*.js"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _log.warning("could not read %s for HTTPS UI rewrite: %s", path, exc)
            continue
        original = text
        for needle, repl in replacements:
            if needle in text:
                text = text.replace(needle, repl)
        if text == original:
            continue
        try:
            path.write_text(text, encoding="utf-8")
            patched += 1
            _log.info("patched HTTPS/same-origin UI URLs in %s", path)
        except OSError as exc:
            _log.warning("could not patch %s for HTTPS UI URLs: %s", path, exc)
    if patched:
        _log.info("patched %s JS file(s) for protocol-aware WS + video_feed URLs", patched)


def _install_latency_hud() -> None:
    """Inject a small RTT / video latency badge into the stock index.html."""
    web_dir = Path(os.environ.get("ANX_ROBOT_WEB_DIR", "/app"))
    if not web_dir.is_dir():
        web_dir = _WEB_DIR
    index = web_dir / "dist" / "index.html"
    if not index.is_file():
        _log.warning("latency HUD: index.html missing at %s", index)
        return
    marker = "/* anx-latency-hud */"
    try:
        html = index.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning("latency HUD: could not read %s: %s", index, exc)
        return
    if marker in html:
        # Still ensure overlay script tags are present.
        extras = ""
        if "/anx/arm_sliders.js" not in html:
            extras += '<script src="/anx/arm_sliders.js" defer></script>'
        if "/anx/power_badge.js" not in html:
            extras += '<script src="/anx/power_badge.js" defer></script>'
        if extras:
            html = html.replace("</body>", extras + "</body>", 1)
            try:
                index.write_text(html, encoding="utf-8")
            except OSError:
                pass
        return
    snippet = (
        f"<script>{marker}\n"
        "(function(){"
        "var el=document.createElement('div');"
        "el.id='anx-latency';"
        "el.style.cssText='position:fixed;top:8px;right:8px;z-index:99999;"
        "background:rgba(0,0,0,.7);color:#9f9;font:12px/1.4 ui-monospace,monospace;"
        "padding:6px 10px;border-radius:4px;pointer-events:none;white-space:pre';"
        "el.textContent='latency: —';"
        "function mount(){if(document.body&&!document.getElementById('anx-latency'))"
        "document.body.appendChild(el);}"
        "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);"
        "else mount();"
        "function paint(rtt,vid){"
        "var parts=['RTT '+(rtt==null?'—':rtt+' ms')];"
        "if(vid!=null)parts.push('video '+vid+' ms');"
        "el.textContent=parts.join(' · ');"
        "var n=rtt==null?999:rtt;"
        "el.style.color=n<80?'#9f9':n<200?'#ff9':'#f99';"
        "}"
        "async function tick(){"
        "var t0=performance.now();"
        "try{"
        "var r=await fetch('/health',{cache:'no-store'});"
        "if(!r.ok)throw new Error('http '+r.status);"
        "await r.json();"
        "paint(Math.round(performance.now()-t0),window.__anxVideoMs);"
        "}catch(e){paint(null,window.__anxVideoMs);el.style.color='#f99';"
        "el.textContent='RTT err · video '+(window.__anxVideoMs==null?'—':window.__anxVideoMs+' ms');}"
        "}"
        "setInterval(tick,1000);tick();"
        "})();</script>"
        '<script src="/anx/arm_sliders.js" defer></script>'
        '<script src="/anx/power_badge.js" defer></script>'
    )
    if "</body>" in html:
        html = html.replace("</body>", snippet + "</body>", 1)
    else:
        html = html + snippet
    try:
        index.write_text(html, encoding="utf-8")
        _log.info("injected latency HUD + arm sliders into %s", index)
    except OSError as exc:
        _log.warning("latency HUD: could not write %s: %s", index, exc)


# Back-compat name used in older call sites / docs.
_install_ws_scheme_rewrite = _install_ui_https_rewrites



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

    def _on_exit(_server=None):
        try:
            from anx_bridge.servos import release_servos

            release_servos()
        except Exception:
            _log.exception("gunicorn on_exit servo release failed")

    options["on_exit"] = _on_exit

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
        from anx_bridge.servos import install_vendor_move_init_patch

        # Re-assert before vendor `scGear.moveInit()` at import time.
        install_vendor_move_init_patch()
    except Exception as exc:
        _log.warning("moveInit patch before webServer import failed: %s", exc)
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
        from anx_bridge.servo_positions import bind_servo_ctrl

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
            bind_servo_ctrl(ctrl)
            if hasattr(ctrl, "initPos") and len(ctrl.initPos) > 0:
                ctrl.initPos[0] = rest
        release_on_controllers(ctrls)
        _log.info(
            "servos released after webServer import (idle until WS connect/control; arm rest init=%s°)",
            rest,
        )
    except Exception as exc:
        _log.warning("servo release after webServer import failed: %s", exc)

    # Overlay-only wrap: idle lights + absolute servoSet:<ch>:<deg> for sliders.
    try:
        from anx_bridge import note_ui_control
        from anx_bridge.servo_positions import parse_ws_servo_set, set_servo_angle
        from anx_bridge.servos import release_servos

        if hasattr(ws_mod, "robotCtrl"):
            _orig_robot_ctrl = ws_mod.robotCtrl

            def _robot_ctrl_with_idle(command_input, response):
                try:
                    note_ui_control(arm_servos=command_input not in ("home",))
                except Exception:
                    pass
                if command_input == "home":
                    # Vendor home drives mid/init into stops → heat. Stay limp.
                    try:
                        release_servos()
                    except Exception:
                        _log.exception("home release failed")
                    return None
                parsed = parse_ws_servo_set(command_input)
                if parsed is not None:
                    ch, deg = parsed
                    try:
                        set_servo_angle(ch, deg)
                    except Exception:
                        _log.exception("servoSet via WS failed ch=%s deg=%s", ch, deg)
                    return None
                return _orig_robot_ctrl(command_input, response)

            ws_mod.robotCtrl = _robot_ctrl_with_idle
            _log.info("wrapped webServer.robotCtrl for idle lights + servoSet + limp home")
    except Exception as exc:
        _log.warning("could not wrap webServer.robotCtrl: %s", exc)

    # Extend get_info with estimated load watts for Hard Ware chips.
    try:
        _install_get_info_battery(ws_mod)
    except Exception as exc:
        _log.warning("get_info power wrap failed: %s", exc)

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
    try:
        from anx_bridge.servos import (
            install_shutdown_release_hooks,
            install_vendor_move_init_patch,
        )

        install_shutdown_release_hooks()
        # Must run before HardwareExecutor / webServer construct ServoCtrl.moveInit.
        install_vendor_move_init_patch()
    except Exception as exc:
        _log.warning("servo shutdown/init hooks not installed: %s", exc)
    # Patch Camera.frames before app.py does `camera = Camera()` (starts the thread).
    _install_camera_frames_guard()
    _load_vendor_app()
    _install_static_fallback()
    _install_health()
    _install_ui_https_rewrites()
    _install_latency_hud()
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
