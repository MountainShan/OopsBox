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
# Unmount sshfs if mounted
WORKDIR="${HOME}/projects/${NAME}"
if mountpoint -q "$WORKDIR" 2>/dev/null; then
  fusermount -u "$WORKDIR" 2>/dev/null && echo "[stop] unmounted sshfs"
fi
# Kill agent window in shared agents session
if tmux list-windows -t agents -F '#{window_name}' 2>/dev/null | grep -qx "$NAME"; then
  tmux kill-window -t "agents:$NAME" 2>/dev/null && echo "[stop] killed agent window"
fi
# Kill terminal tmux session
tmux kill-session -t "term-${NAME}" 2>/dev/null && echo "[stop] killed terminal session"
# Stop isolation container if running
CONTAINER="oopsbox-agent-${NAME}"
for rt in podman docker; do
  if command -v $rt &>/dev/null && $rt ps -q --filter "name=$CONTAINER" 2>/dev/null | grep -q .; then
    $rt stop "$CONTAINER" 2>/dev/null && $rt rm "$CONTAINER" 2>/dev/null && echo "[stop] removed container $CONTAINER"
    break
  fi
done
echo "[stop] project stopped."
