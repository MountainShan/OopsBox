#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
PID_DIR="/tmp/rcoder-${NAME}"

for SVC in ttyd; do
  PIDFILE="$PID_DIR/${SVC}.pid"
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    kill -0 "$PID" 2>/dev/null && kill "$PID" && echo "[stop] killed $SVC"
    rm -f "$PIDFILE"
  fi
done
# Kill agent window in shared agents session
if tmux list-windows -t agents -F '#{window_name}' 2>/dev/null | grep -qx "$NAME"; then
  tmux kill-window -t "agents:$NAME" 2>/dev/null && echo "[stop] killed agent window"
fi
echo "[stop] project stopped."
