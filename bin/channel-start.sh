#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
SESSION="chan-${NAME}"
REGISTRY="/home/mountain/projects/.channel-registry.json"

[ -f "$REGISTRY" ] || { echo "ERROR: channel registry not found" >&2; exit 1; }

WORKDIR=$(jq -r --arg n "$NAME" '.[$n].workdir // "/home/mountain/channels/'"$NAME"'"' "$REGISTRY")
SKIP_PERMS=$(jq -r --arg n "$NAME" '.[$n].skip_permissions // false' "$REGISTRY")
TG_TOKEN=$(jq -r --arg n "$NAME" '.[$n].telegram_token // ""' "$REGISTRY")

# Ensure workdir exists
mkdir -p "$WORKDIR"

echo "[channel-start] $NAME — workdir:${WORKDIR}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[channel-start] session already running"
  exit 0
fi

tmux new-session -d -s "$SESSION" -c "$WORKDIR"
tmux rename-window -t "$SESSION" "claude"

FLAGS="--trust-project -n chan-${NAME} --channels plugin:telegram@claude-plugins-official"
if [ "$SKIP_PERMS" = "true" ]; then
  FLAGS="$FLAGS --dangerously-skip-permissions"
fi

# Start Claude loop
tmux send-keys -t "$SESSION:claude" \
  "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; while true; do claude --continue $FLAGS 2>/dev/null || claude $FLAGS; echo '[channel] restarting in 2s...'; sleep 2; done" Enter

# Auto-configure Telegram token and set open access
if [ -n "$TG_TOKEN" ] && [ "$TG_TOKEN" != "null" ]; then
  (
    sleep 10
    tmux send-keys -t "$SESSION:claude" "/telegram:configure ${TG_TOKEN}" Enter
    sleep 3
    tmux send-keys -t "$SESSION:claude" "/telegram:access policy open" Enter
  ) &
fi

echo "[channel-start] '$NAME' running"
