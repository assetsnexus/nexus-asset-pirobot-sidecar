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

# Drop PCA9685 PWM before kill so servos go limp (otherwise they keep holding heat).
if docker compose ps --status running -q 2>/dev/null | grep -q .; then
  echo "Releasing servos (PWM off) before stop..."
  docker compose exec -T anx-robot python -c '
import sys
sys.path.insert(0, "/overlay")
from anx_bridge.servos import release_servos
release_servos()
print("servos released")
' 2>/dev/null || echo "warn: could not exec servo release (container may already be stopping)" >&2
fi

if [[ ${#args[@]} -gt 0 ]]; then
  docker compose down "${args[@]}"
else
  docker compose down
fi

echo "Sidecar stopped. Edge node data was not touched."
