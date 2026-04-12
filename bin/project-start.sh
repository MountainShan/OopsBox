#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="${HOME}/projects/${NAME}"
PID_DIR="/tmp/oopsbox-${NAME}"

[ -d "$WORKDIR" ] || { echo "ERROR: project '$NAME' not found" >&2; exit 1; }
mkdir -p "$PID_DIR"

# Find a free port for ttyd
TTYD_PORT=$(python3 -c "
import socket
with socket.socket() as s:
    s.bind(('', 0))
    print(s.getsockname()[1])
")

echo "[start] $NAME — ttyd port: $TTYD_PORT"
echo "$TTYD_PORT" > "$PID_DIR/ttyd.port"

# Ensure agents tmux session exists
if ! tmux has-session -t agents 2>/dev/null; then
  tmux new-session -d -s agents -n system
fi

# Create project window in tmux
if ! tmux list-windows -t agents -F '#W' 2>/dev/null | grep -q "^${NAME}$"; then
  tmux new-window -t agents -n "$NAME" -c "$WORKDIR"
fi

# Start ttyd for this project
ttyd \
  --port "$TTYD_PORT" \
  --base-path "/terminal/${NAME}" \
  --writable \
  -- "$HOME/bin/project-term.sh" "$NAME" "$WORKDIR" \
  > "$PID_DIR/ttyd.log" 2>&1 &

echo $! > "$PID_DIR/ttyd.pid"

# Update nginx config and reload
"$HOME/bin/nginx-update-projects.sh"

echo "[start] $NAME — done (ttyd pid: $(cat $PID_DIR/ttyd.pid))"
