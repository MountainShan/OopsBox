#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
SESSION="chan-${NAME}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux kill-session -t "$SESSION"
  echo "[channel-stop] '$NAME' stopped"
else
  echo "[channel-stop] '$NAME' not running"
fi
