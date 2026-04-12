#!/usr/bin/env bash
# Attach browser terminal to project's isolated tmux session
set -euo pipefail
NAME="$1"
WORKDIR="${2:-$HOME/projects/$NAME}"
SESSION="oopsbox-${NAME}"

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -c "$WORKDIR"
fi

exec tmux attach -t "$SESSION"
