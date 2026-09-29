#!/usr/bin/env bash
# Stop the robot sidecar. Does not delete volumes or the edge node's data.
# -v / --volumes is ignored.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -v|--volumes|--volumes=*)
      echo "Ignoring $1 — down.sh does not delete volumes or data." >&2
      shift
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

if [[ ${#args[@]} -gt 0 ]]; then
  docker compose down "${args[@]}"
else
  docker compose down
fi

echo "Sidecar stopped. Edge node data was not touched."
