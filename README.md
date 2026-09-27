# ANX RaspTank robot sidecar

OEM control + camera UI that **joins** [`asset-node-ipc-docker`](../asset-node-ipc-docker/) — same Docker network (`anx-assets-ipc-network`), same Mosquitto (`oem` profile), same env names (`MQTT_BROKER`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_CA_FILE`). This tree is not a second asset-node stack and does **not** run its own broker.

Controller topic contract: [MQTT_CONTROLLER_CONTRACT.md](./MQTT_CONTROLLER_CONTRACT.md).

Vendor sources are the git submodule `robot_control/adeept_rasptank2`, pinned to public commit `f5fe667` (`Add files via upload` on [adeept/adeept_rasptank2](https://github.com/adeept/adeept_rasptank2)). `origin` stays that GitHub repo. Do not move the pin to local commit `50860d1`, and do not push ANX commits to Adeept. ANX behaviour is the overlay under `robot_control/anx-robot/`:

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

A USB gamepad uses the Xbox map in `robot_control/anx-robot/anx_bridge/controller_map.xbox.json` (`evdev`, `/dev/input`). MQTT clients publish on `{ANX_TOPIC_PREFIX}/controller/+` — see [MQTT_CONTROLLER_CONTRACT.md](./MQTT_CONTROLLER_CONTRACT.md). Check `http://127.0.0.1:5000/health`.

Joining the ANX IPC Mosquitto stack is below.

## Deploy order (prerequisite)

MQTT join needs the IPC broker and CA **before** this sidecar. This is an ops order requirement, not a missing Docker image:

```bash
# 1) IPC stack with oem (Mosquitto TLS + passwd + ca.crt)
cd ../asset-node-ipc-docker
# COMPOSE_PROFILES must include oem (or oem-io), e.g. registry-db,oem
./prepare.sh
docker compose up -d
# Confirm: data/mqtt/certs/ca.crt exists; network anx-assets-ipc-network exists

# 2) This sidecar
cd ../asset-demo-pirobot-sidecar
./prepare.sh          # .env + gitignored web/.env from overlay; copies MQTT_PASSWORD from ipc
# Enable bridge once ipc oem is healthy:
#   set ANX_BRIDGE_ENABLED=true in .env (and re-run ./prepare.sh to sync web/.env)
docker compose up -d  # pulls published anx-robot-sidecar image
curl -sf http://127.0.0.1:5000/health
```

Web UI: `http://<pi>:5000` (password from `.env` / `ROBOT_CONTROL_PASSWORD`).

## How the MQTT bridge starts

1. Process entry is `robot_control/anx-robot/anx_web_entry.py` (Docker `CMD` / systemd `ExecStart`).
2. It loads vendor `app.py` without modifying it, then calls `anx_bridge.start_bridge()` when `ANX_BRIDGE_ENABLED` is true/1/yes.
3. Config reads **ipc** names first: `MQTT_BROKER`, `MQTT_USER`, `MQTT_PASSWORD`, `MQTT_CA_FILE` (aliases `ANX_MQTT_*` still work).
4. Subscribes to `{ANX_TOPIC_PREFIX}/cmd` and `{ANX_TOPIC_PREFIX}/controller/+`, publishes `{prefix}/telemetry` and `{prefix}/availability`.
5. Hardware commands go through an overlay executor that imports vendor `move` / `switch` / `RPIservo` when present; without GPIO the bridge still MQTT-connects and logs actions.

## Image publish (not done here)

| Image | Tag | Built from |
|-------|-----|------------|
| `eu1.dockerreg.sdk.assetsnexus.org/anx-robot-sidecar` | `latest` | `Dockerfile` (vendor web + `anx-robot/` overlay) |

Until that image is published, `docker compose up` cannot pull it. Do not commit registry credentials.

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
| `ANX_DEADMAN_MS` | Stop drive/servos after quiet input (default 500) |
| `IPC_DOCKER_NETWORK` | Must match ipc compose network name |

## Healthcheck

Compose probes `GET /health` on port 5000 (unauthenticated JSON `{ ok: true }`), served by `robot_control/anx-robot/anx_web_entry.py` — not by editing vendor `app.py`.

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
| `docker-compose.yml` | Sidecar service + external ipc network |
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
