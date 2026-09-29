#!/usr/bin/env bash
# Locate the edge-node checkout that already wrote data/mqtt/certs/ca.crt.
# Sourced by up.sh and prepare.sh. Sets IPC_DIR and IPC_MQTT_CERTS_DIR.
#
# The published clone directory is nexus-asset-example-docker-dev.
# asset-node-ipc-docker is the name used inside this monorepo.

resolve_ipc_checkout() {
  local root="$1"
  local -a looked=()
  IPC_LOOKED=""

  _abs() {
    (cd "$1" 2>/dev/null && pwd) || true
  }

  _try() {
    local candidate="$1"
    local abs
    abs="$(_abs "$candidate")"
    if [[ -z "$abs" ]]; then
      looked+=("$candidate")
      return 1
    fi
    looked+=("$abs")
    if [[ -f "$abs/data/mqtt/certs/ca.crt" ]]; then
      IPC_DIR="$abs"
      IPC_MQTT_CERTS_DIR="$abs/data/mqtt/certs"
      return 0
    fi
    return 1
  }

  if [[ -n "${IPC_DIR:-}" ]]; then
    _try "$IPC_DIR" && return 0
  fi

  _try "$root/../nexus-asset-example-docker-dev" && return 0
  _try "$root/../asset-node-ipc-docker" && return 0

  local sib abs
  for sib in "$root"/../*; do
    [[ -d "$sib" ]] || continue
    abs="$(_abs "$sib")"
    [[ -n "$abs" && "$abs" == "$root" ]] && continue
    _try "$sib" && return 0
  done

  IPC_LOOKED="$(printf '  %s\n' "${looked[@]}")"
  return 1
}
