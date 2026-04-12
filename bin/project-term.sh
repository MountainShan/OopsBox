#!/usr/bin/env bash
# Attach browser terminal to project's tmux window
# Usage: project-term.sh <project-name> <workdir>
set -euo pipefail
NAME="$1"
WORKDIR="${2:-$HOME/projects/$NAME}"

if ! tmux has-session -t agents 2>/dev/null; then
  tmux new-session -d -s agents -n system
fi

if ! tmux list-windows -t agents -F '#W' 2>/dev/null | grep -q "^${NAME}$"; then
  tmux new-window -t agents -n "$NAME" -c "$WORKDIR"
fi

exec tmux attach -t "agents:$NAME"
