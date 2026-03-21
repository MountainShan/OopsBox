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
echo "[stop] services stopped. tmux session preserved."
