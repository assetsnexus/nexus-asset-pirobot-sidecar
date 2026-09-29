#!/usr/bin/env bash
# Build the sidecar locally and join the edge node. No image registry.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

IPC_DIR="${IPC_DIR:-$ROOT/../asset-node-ipc-docker}"
CA="${IPC_MQTT_CERTS_DIR:-$IPC_DIR/data/mqtt/certs}/ca.crt"

if [[ ! -f "$CA" ]]; then
  echo "ERROR: MQTT CA missing ($CA)." >&2
  echo "Start the edge node first:  cd ${IPC_DIR} && ./up.sh" >&2
  exit 1
fi

if [[ ! -f robot_control/adeept_rasptank2/web/app.py ]]; then
  echo "ERROR: vendor web tree missing. Clone with: git clone --recurse-submodules" >&2
  exit 1
fi

./prepare.sh

set_key() {
  local key="$1"
  local val="$2"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

# Join the edge stack. Do not copy the host MQTT URL (127.0.0.1) into this container.
set_key ANX_BRIDGE_ENABLED true
set_key MQTT_BROKER "mqtts://mqtt:8883"
set_key MQTT_USER anx
set_key MQTT_CA_FILE "/certs/ca.crt"
set_key IPC_DOCKER_NETWORK anx-assets-ipc-network
set_key ANX_TOPIC_PREFIX rasptank

docker compose up -d --build

PORT="$(grep -E '^ROBOT_HTTP_PORT=' .env | head -1 | cut -d= -f2- || true)"
PORT="${PORT:-5000}"
echo ""
echo "Robot sidecar is up (image built locally, no registry)."
echo "Health: curl -sf http://127.0.0.1:${PORT}/health"
echo "Pair the edge node with any one method: manual ZIP, USB, Bluetooth, or pairing link."
