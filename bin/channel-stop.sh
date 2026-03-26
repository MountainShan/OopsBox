#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WINDOW="chan-${NAME}"

if tmux list-windows -t agents -F '#{window_name}' 2>/dev/null | grep -qx "$WINDOW"; then
  tmux send-keys -t "agents:$WINDOW" C-c 2>/dev/null
  sleep 1
  tmux kill-window -t "agents:$WINDOW" 2>/dev/null
  echo "[channel-stop] '$NAME' stopped"
else
  echo "[channel-stop] '$NAME' not running"
fi
