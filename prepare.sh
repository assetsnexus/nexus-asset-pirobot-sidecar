#!/usr/bin/env bash
# Prepare the RaspTank sidecar env to join asset-node-ipc-docker (oem MQTT).
# Does not modify the adeept_rasptank2 submodule working tree.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/resolve-ipc-dir.sh"
if [[ -z "${IPC_DIR:-}" ]]; then
  resolve_ipc_checkout "$ROOT" || IPC_DIR="$ROOT/../nexus-asset-example-docker-dev"
fi
export IPC_DIR IPC_MQTT_CERTS_DIR
WEB_DIR="$ROOT/robot_control/adeept_rasptank2/web"
WEB_ENV="$WEB_DIR/.env"
OVERLAY_ENV_EXAMPLE="$ROOT/robot_control/anx-robot/web.env.example"

gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  else
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

set_env_if_empty() {
  local key="$1"
  local val="$2"
  if grep -qE "^${key}=$" .env 2>/dev/null || ! grep -qE "^${key}=" .env 2>/dev/null; then
    if grep -qE "^${key}=" .env 2>/dev/null; then
      sed -i "s|^${key}=.*|${key}=${val}|" .env
    else
      echo "${key}=${val}" >> .env
    fi
    echo "  generated ${key}"
  elif grep -qE "^${key}=.+" .env 2>/dev/null; then
    echo "  kept existing ${key}"
  fi
}

set_key_in_file() {
  local file="$1"
  local key="$2"
  local val="$3"
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    local esc
    esc=$(printf '%s' "$val" | sed -e 's/[&|\\]/\\&/g')
    sed -i "s|^${key}=.*|${key}=${esc}|" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

# Read one KEY=value line. Do not source the edge .env: it is a Compose env
# file, and a later line can abort `source` before MQTT_PASSWORD is copied.
env_file_value() {
  local file="$1"
  local key="$2"
  local line
  line="$(grep -E "^${key}=" "$file" 2>/dev/null | head -1 || true)"
  line="${line%%$'\r'}"
  line="${line#"${key}="}"
  line="${line#"\""}"
  line="${line%"\""}"
  printf '%s' "$line"
}

echo "==> ANX RaspTank sidecar prepare"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "  created .env from .env.example"
else
  echo "  using existing .env"
fi

set_env_if_empty ROBOT_CONTROL_PASSWORD "$(gen_secret)"

side_mqtt="$(env_file_value .env MQTT_PASSWORD)"
if [[ -z "$side_mqtt" ]]; then
  if [[ ! -f "$IPC_DIR/.env" ]]; then
    echo "ERROR: edge checkout has no .env at ${IPC_DIR}/.env" >&2
    exit 1
  fi
  ipc_mqtt="$(env_file_value "$IPC_DIR/.env" MQTT_PASSWORD)"
  if [[ -z "$ipc_mqtt" ]]; then
    echo "ERROR: MQTT_PASSWORD is empty in ${IPC_DIR}/.env" >&2
    echo "Re-run ./up.sh in that edge checkout so prepare.sh generates the Mosquitto password, then run this again." >&2
    exit 1
  fi
  set_key_in_file .env MQTT_PASSWORD "$ipc_mqtt"
  echo "  copied MQTT_PASSWORD from ${IPC_DIR}/.env"
else
  echo "  kept existing MQTT_PASSWORD"
fi

# shellcheck disable=SC1091
set -a
# shellcheck source=/dev/null
source .env
set +a

# Vendor web/.env is gitignored (submodule + parent). Seed from ANX overlay example, never edit vendor .env.example.
if [[ ! -d "$WEB_DIR" ]]; then
  echo "ERROR: vendor web dir missing ($WEB_DIR) — init the adeept_rasptank2 submodule" >&2
  exit 1
fi

if [[ ! -f "$WEB_ENV" ]]; then
  if [[ -f "$OVERLAY_ENV_EXAMPLE" ]]; then
    cp "$OVERLAY_ENV_EXAMPLE" "$WEB_ENV"
    echo "  created ${WEB_ENV} from robot_control/anx-robot/web.env.example (overlay)"
  elif [[ -f "$WEB_DIR/.env.example" ]]; then
    cp "$WEB_DIR/.env.example" "$WEB_ENV"
    echo "  created ${WEB_ENV} from vendor .env.example"
  else
    echo "ROBOT_CONTROL_PASSWORD=" > "$WEB_ENV"
    echo "  created empty ${WEB_ENV}"
  fi
fi

if [[ -n "${ROBOT_CONTROL_PASSWORD:-}" ]]; then
  set_key_in_file "$WEB_ENV" ROBOT_CONTROL_PASSWORD "$ROBOT_CONTROL_PASSWORD"
  echo "  synced ROBOT_CONTROL_PASSWORD into web/.env (gitignored)"
fi
# Mirror MQTT join vars into web/.env for native (non-Docker) runs
for k in MQTT_BROKER MQTT_USER MQTT_PASSWORD MQTT_CA_FILE ANX_BRIDGE_ENABLED ANX_CONTROL_SOURCE ANX_DEADMAN_MS ANX_TOPIC_PREFIX; do
  eval "v=\${$k:-}"
  if [[ -n "$v" ]]; then
    set_key_in_file "$WEB_ENV" "$k" "$v"
  fi
done
echo "  synced MQTT/ANX join keys into web/.env (gitignored; submodule untouched)"

# .env may still name the old sibling folder. Prefer the checkout that has the CA.
if ! resolve_ipc_checkout "$ROOT"; then
  :
fi
if [[ -n "${IPC_MQTT_CERTS_DIR:-}" ]]; then
  set_key_in_file .env IPC_MQTT_CERTS_DIR "$IPC_MQTT_CERTS_DIR"
fi
CA="${IPC_MQTT_CERTS_DIR:-$IPC_DIR/data/mqtt/certs}/ca.crt"
if [[ -f "$CA" ]]; then
  echo "  MQTT CA present: $CA"
else
  echo "WARN: MQTT CA not found next to this checkout (looked for $CA)." >&2
  echo "  The edge node writes it at data/mqtt/certs/ca.crt inside its own checkout (sibling nexus-asset-example-docker-dev)." >&2
fi

if [[ -z "${ROBOT_CONTROL_PASSWORD:-}" ]]; then
  echo "ERROR: ROBOT_CONTROL_PASSWORD still empty" >&2
  exit 1
fi

# Sanity: submodule must stay clean (no ANX edits in vendor tree)
if command -v git >/dev/null 2>&1 && [[ -d "$ROOT/robot_control/adeept_rasptank2/.git" || -f "$ROOT/robot_control/adeept_rasptank2/.git" ]]; then
  if ! git -C "$ROOT/robot_control/adeept_rasptank2" diff --quiet; then
    echo "WARN: adeept_rasptank2 working tree is dirty — ANX overlays must not live in the submodule" >&2
    git -C "$ROOT/robot_control/adeept_rasptank2" status -sb >&2
  else
    echo "  submodule adeept_rasptank2: clean"
  fi
fi

echo ""
echo "Next: ./up.sh   # builds the image locally and starts the sidecar"
echo "Done."
