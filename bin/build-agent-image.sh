#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Detect runtime: podman preferred, fallback to docker
if command -v podman &>/dev/null; then
  RUNTIME="podman"
elif command -v docker &>/dev/null; then
  RUNTIME="docker"
else
  echo "[build-agent] ERROR: neither podman nor docker found" >&2
  exit 1
fi

AGENT_UID=$(id -u)
echo "[build-agent] Building oopsbox-agent image (UID=$AGENT_UID) with $RUNTIME..."

$RUNTIME build \
  -t oopsbox-agent \
  --build-arg AGENT_UID="$AGENT_UID" \
  -f "$PROJECT_ROOT/docker/Containerfile.agent" \
  "$PROJECT_ROOT"

echo "[build-agent] oopsbox-agent image ready"
