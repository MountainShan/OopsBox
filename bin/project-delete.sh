#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="${HOME}/projects/${NAME}"
SESSION="proj-${NAME}"

$HOME/bin/project-stop.sh "$NAME" 2>/dev/null || true
tmux kill-session -t "$SESSION" 2>/dev/null || true
rm -rf "$WORKDIR"

STATE_FILE="${HOME}/projects/.port-registry.json"
[ -f "$STATE_FILE" ] && \
  jq --arg n "$NAME" 'del(.[$n])' "$STATE_FILE" > "$STATE_FILE.tmp" && \
  mv "$STATE_FILE.tmp" "$STATE_FILE"

rm -rf "/tmp/rcoder-${NAME}"
$HOME/bin/nginx-reload-ports.sh 2>/dev/null || true
echo "[delete] project '$NAME' removed"
