# ANX RaspTank robot sidecar

OEM control + camera UI that **joins** [`asset-node-ipc-docker`](../asset-node-ipc-docker/) — same Docker network (`anx-assets-ipc-network`), same Mosquitto (`oem` profile), same env names (`MQTT_BROKER`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_CA_FILE`). This tree is not a second asset-node stack and does **not** run its own broker.

Controller topic contract: [MQTT_CONTROLLER_CONTRACT.md](./MQTT_CONTROLLER_CONTRACT.md).

Vendor sources are the git submodule `robot_control/adeept_rasptank2`, pinned to public commit `f5fe667` (`Add files via upload` on [adeept/adeept_rasptank2](https://github.com/adeept/adeept_rasptank2)). `origin` stays that GitHub repo. Do not record unpublished local commits as the pin (Pi hosts cannot fetch them), do not move the pin to local commit `50860d1`, and do not push ANX commits to Adeept. ANX behaviour is the overlay under `robot_control/anx-robot/`:

- `anx_web_entry.py` — `/health` + starts the MQTT bridge
- `anx_bridge/` — MQTT subscribe/publish + USB/MQTT controller mapping

## Standalone

Run the overlay on its own. Leave the vendor submodule `robot_control/adeept_rasptank2` at the public Adeept pin. `robot_control/anx-robot/` extends that tree with MQTT and a USB game controller; it loads vendor `app.py` and does not patch vendor files.

```bash
# Vendor pin. Skip when robot_control/adeept_rasptank2 is already checked out.
git clone https://github.com/adeept/adeept_rasptank2.git robot_control/adeept_rasptank2
git -C robot_control/adeept_rasptank2 checkout f5fe667

cp .env.example .env
# ANX_BRIDGE_ENABLED=true
# ANX_CONTROL_SOURCE=auto   # USB gamepad when plugged in, otherwise MQTT
# MQTT_BROKER=mqtt://127.0.0.1:1883
# ANX_MQTT_INSECURE=true    # local plaintext broker only

pip install -r robot_control/anx-robot/requirements-bridge.txt
python robot_control/anx-robot/anx_web_entry.py
```

A USB gamepad uses the Xbox map in `robot_control/anx-robot/anx_bridge/controller_map.xbox.json` (`evdev`, `/dev/input`). MQTT clients publish on `{ANX_TOPIC_PREFIX}/controller/+` — see [MQTT_CONTROLLER_CONTRACT.md](./MQTT_CONTROLLER_CONTRACT.md). Check `https://127.0.0.1:5000/health` (`curl -sk`).

Joining the ANX IPC Mosquitto stack is below.

## Ship (after the edge node)

The edge node must already be up (`./up.sh` in the sibling checkout `nexus-asset-example-docker-dev`). This sidecar finds that tree by `data/mqtt/certs/ca.crt`, joins its Docker network, and uses Mosquitto user `anx`. No env edits. This command builds the image on the Pi. No Docker registry.

```bash
git clone --recurse-submodules https://github.com/assetsnexus/nexus-asset-pirobot-sidecar.git
cd nexus-asset-pirobot-sidecar
./up.sh
curl -sk https://127.0.0.1:5000/health
```

`./up.sh` copies the MQTT password from the edge node, turns the bridge on, and runs `docker compose up -d --build`. Then pair the edge node with any one of the four methods in the IPC README (manual ZIP, USB, Bluetooth, pairing link).

Web UI: `https://<pi>:5000` (self-signed cert from `./up.sh` → `data/certs/`). Control uses `wss://<pi>:8888`. Stock Adeept WS login is `admin` / `123456`. Set `ANX_ROBOT_TLS=false` to fall back to plain HTTP/WS.

## How the MQTT bridge starts

1. Process entry is `robot_control/anx-robot/anx_web_entry.py` (Docker `CMD` / systemd `ExecStart`).
2. It loads vendor `app.py` without modifying it, then calls `anx_bridge.start_bridge()` when `ANX_BRIDGE_ENABLED` is true/1/yes.
3. Config reads **ipc** names first: `MQTT_BROKER`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_CA_FILE` (aliases `ANX_MQTT_*` still work).
4. Subscribes to `{ANX_TOPIC_PREFIX}/cmd`, `{ANX_TOPIC_PREFIX}/controller/+`, and `{prefix}/node_heartbeat`; publishes `{prefix}/telemetry` (~1 Hz), `{prefix}/telemetry_fast` (range ~20 Hz), and `{prefix}/availability`.
5. Hardware commands go through an overlay executor that imports vendor `move` / `switch` / `RPIservo` / `robotLight` when present; without GPIO the bridge still MQTT-connects and logs actions. Timed motions (`drive_cm`, `turn_*_90`, `drive_sequence`) and open-loop odometry live only in `anx-robot/` (vendor tree untouched).

## Image

`docker compose up` builds `anx-robot-sidecar:local` from the `Dockerfile` in this directory (vendor web + `anx-robot/` overlay). Nothing is pulled from a registry. The image includes Adafruit Blinka (`board`), the PCA9685 motor driver, gpiozero, and `liblgpio1`. `./up.sh` passes the host `/dev/gpiochip*` and `/dev/i2c-*` nodes into the container. Privileged mode alone does not create those nodes.

Optional Edge AI on the same IPC host uses the ipc example only (`./prepare.sh --with-inference` there).

## Env alignment with ipc-docker

| Variable | Role |
|----------|------|
| `MQTT_BROKER` | Default `mqtts://mqtt:8883` on the compose network |
| `MQTT_USER` / `MQTT_PASSWORD` | Same as ipc `prepare.sh` Mosquitto user `anx` |
| `MQTT_CA_FILE` | `/certs/ca.crt` (bind from ipc `data/mqtt/certs/ca.crt`) |
| `ANX_BRIDGE_ENABLED` | `true` to start the overlay MQTT bridge |
| `ANX_CONTROL_SOURCE` | `auto` \| `usb` \| `mqtt` |
| `ANX_TOPIC_PREFIX` | Default `rasptank` (blueprint topic prefix) |
| `ANX_DEADMAN_MS` | Quiet-MQTT failsafe (default 500); gated during timed motion; suppressed while node heartbeats are fresh |
| `ANX_NODE_HEARTBEAT_MS` | Node-heartbeat freshness window (default 2000); node owns session timers while fresh |
| `ANX_OBSTACLE_STOP_MM` | Local ultrasonic stop threshold during timed motion (default 100) |
| `ANX_WHEEL_DIAMETER_M` / `ANX_TRACK_WIDTH_M` / `ANX_SPEED_AT_FULL_PWM_MPS` | Open-loop odometry defaults `0.045` / `0.12` / `0.35` |
| `IPC_DOCKER_NETWORK` | Must match ipc compose network name |

## Healthcheck

Compose probes `GET /health` on port 5000 (unauthenticated JSON `{ ok: true }`), served by `robot_control/anx-robot/anx_web_entry.py` — not by editing vendor `app.py`. The image installs Raspberry Pi `python3-opencv` / `python3-picamera2` / `python3-libcamera` so Adeept `app.py` can import; if that still fails, the overlay serves `web/dist` static UI so `/` is not a blank 404. `./up.sh` maps `/dev/video*` `/dev/media*` `/dev/dma_heap*` and mounts `/run/udev` so Picamera2 can see the CSI camera; without those, libcamera's camera list is empty and the overlay serves a placeholder `/video_feed` instead of crashing the camera thread.

## Native (non-Docker) on the Pi

1. Complete the IPC oem steps above (or point MQTT_* at a reachable broker + CA).
2. `./prepare.sh`
3. `pip install -r robot_control/anx-robot/requirements-bridge.txt` (upstream `f5fe667` has no `requirements.txt`)
4. On the Pi, also install the vendor camera/GPIO stack (picamera2, OpenCV, adafruit-circuitpython-pca9685, gpiozero). The overlay still serves `/health` and MQTT if those imports fail.
5. Set `ANX_BRIDGE_ENABLED=true` in `.env` / `web/.env`, then:
   `python robot_control/anx-robot/anx_web_entry.py` or install `robot_control/anx-robot/anx-robot.service`.

## Files

| Path | Role |
|------|------|
| `up.sh` | One command: prepare, build locally, start |
| `docker-compose.yml` | Sidecar service + external ipc network (`pull_policy: build`) |
| `Dockerfile` | Image recipe (vendor web + overlay entry + `anx_bridge`) |
| `prepare.sh` | `.env` + gitignored `web/.env`; never dirties the submodule |
| `.env.example` | Documented defaults (no secrets) |
| `MQTT_CONTROLLER_CONTRACT.md` | Topic / payload contract |
| `robot_control/anx-robot/anx_web_entry.py` | `/health` + bridge start overlay |
| `robot_control/anx-robot/anx_bridge/` | MQTT/USB bridge package |
| `robot_control/anx-robot/requirements-bridge.txt` | `paho-mqtt` (+ optional `evdev`) |
| `robot_control/anx-robot/web.env.example` | Seed for gitignored vendor `web/.env` |
| `robot_control/anx-robot/anx-robot.service` | Optional systemd unit |
| `robot_control/adeept_rasptank2/` | Upstream pin `f5fe667` (public Adeept; leave clean) |
