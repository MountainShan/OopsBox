#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
SESSION="chan-${NAME}"
REGISTRY="/home/mountain/projects/.channel-registry.json"

[ -f "$REGISTRY" ] || { echo "ERROR: channel registry not found" >&2; exit 1; }

WORKDIR=$(jq -r --arg n "$NAME" '.[$n].workdir // "/home/mountain"' "$REGISTRY")
SKIP_PERMS=$(jq -r --arg n "$NAME" '.[$n].skip_permissions // false' "$REGISTRY")

echo "[channel-start] $NAME — workdir:${WORKDIR}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[channel-start] session already running"
  exit 0
fi

tmux new-session -d -s "$SESSION" -c "$WORKDIR"
tmux rename-window -t "$SESSION" "claude"

FLAGS="-n chan-${NAME} --channels plugin:telegram@claude-plugins-official"
if [ "$SKIP_PERMS" = "true" ]; then
  FLAGS="$FLAGS --dangerously-skip-permissions"
fi

tmux send-keys -t "$SESSION:claude" \
  "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; while true; do claude --continue $FLAGS 2>/dev/null || claude $FLAGS; echo '[channel] restarting in 2s...'; sleep 2; done" Enter

echo "[channel-start] '$NAME' running"
