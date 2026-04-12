#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 1 ]] || { echo "Usage: $(basename "$0") <project-name>" >&2; exit 1; }
NAME="$1"
PID_DIR="/tmp/oopsbox-${NAME}"
SESSION="oopsbox-${NAME}"

echo "[stop] $NAME"

if [ -f "$PID_DIR/ttyd.pid" ]; then
  PID=$(cat "$PID_DIR/ttyd.pid")
  if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null && grep -q ttyd /proc/"$PID"/cmdline 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
  fi
  rm -f "$PID_DIR/ttyd.pid"
fi

tmux kill-session -t "$SESSION" 2>/dev/null || true
rm -f "$PID_DIR/ttyd.port"

"$HOME/bin/nginx-update-projects.sh" 2>/dev/null || true

echo "[stop] $NAME — done"
