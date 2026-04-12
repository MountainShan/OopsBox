#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
PID_DIR="/tmp/oopsbox-${NAME}"

echo "[stop] $NAME"

if [ -f "$PID_DIR/ttyd.pid" ]; then
  PID=$(cat "$PID_DIR/ttyd.pid")
  kill "$PID" 2>/dev/null || true
  rm -f "$PID_DIR/ttyd.pid"
fi

tmux kill-window -t "agents:${NAME}" 2>/dev/null || true
rm -f "$PID_DIR/ttyd.port"

"$HOME/bin/nginx-update-projects.sh" 2>/dev/null || true

echo "[stop] $NAME — done"
