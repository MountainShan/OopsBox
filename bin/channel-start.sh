#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WINDOW="chan-${NAME}"
REGISTRY="/home/mountain/projects/.channel-registry.json"

[ -f "$REGISTRY" ] || { echo "ERROR: channel registry not found" >&2; exit 1; }

WORKDIR=$(jq -r --arg n "$NAME" '.[$n].workdir // "/home/mountain/channels/'"$NAME"'"' "$REGISTRY")
TG_TOKEN=$(jq -r --arg n "$NAME" '.[$n].telegram_token // ""' "$REGISTRY")

# Ensure workdir exists and is pre-trusted
mkdir -p "$WORKDIR/.claude"
[ -f "$WORKDIR/.claude/settings.local.json" ] || echo '{}' > "$WORKDIR/.claude/settings.local.json"

# Pre-write Telegram token
if [ -n "$TG_TOKEN" ] && [ "$TG_TOKEN" != "null" ]; then
  mkdir -p "$HOME/.claude/channels/telegram"
  echo "TELEGRAM_BOT_TOKEN=${TG_TOKEN}" > "$HOME/.claude/channels/telegram/.env"
  chmod 600 "$HOME/.claude/channels/telegram/.env"
  if [ ! -f "$HOME/.claude/channels/telegram/access.json" ]; then
    echo '{"dm_policy":"pairing","allowed_senders":[],"pending_pairings":{}}' > "$HOME/.claude/channels/telegram/access.json"
  fi
fi

echo "[channel-start] $NAME — workdir:${WORKDIR}"

# Ensure agents session exists
if ! tmux has-session -t agents 2>/dev/null; then
  tmux new-session -d -s agents -c "$HOME" -n "system"
fi

# Check if window already exists
if tmux list-windows -t agents -F '#{window_name}' | grep -qx "$WINDOW"; then
  echo "[channel-start] window already exists"
  exit 0
fi

# Create window in agents session
tmux new-window -t agents -n "$WINDOW" -c "$WORKDIR"
tmux resize-window -t "agents:$WINDOW" -x 300 -y 80 2>/dev/null || true

# Channels always need skip permissions
FLAGS="--dangerously-skip-permissions --allow-dangerously-skip-permissions -n $WINDOW --channels plugin:telegram@claude-plugins-official"

SID_FILE="/tmp/claude-loop-session-${WINDOW}.id"
tmux send-keys -t "agents:$WINDOW" \
  "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; SID_FILE=${SID_FILE}; while true; do if [ -f \"\$SID_FILE\" ]; then SID=\$(cat \"\$SID_FILE\"); claude --resume \"\$SID\" $FLAGS || { rm -f \"\$SID_FILE\"; claude $FLAGS; }; else claude $FLAGS; fi; HASH_DIR=\$(echo \"\$PWD\" | sed 's/[^a-zA-Z0-9]/-/g'); LATEST=\$(ls -t \"\$HOME/.claude/projects/\$HASH_DIR\"/*.jsonl 2>/dev/null | head -1); [ -n \"\$LATEST\" ] && basename \"\$LATEST\" .jsonl > \"\$SID_FILE\"; echo '[channel] restarting in 2s...'; sleep 2; done" Enter

# Auto-confirm bypass permissions dialog
(
  for i in 1 2 3 4 5; do
    sleep 3
    tmux send-keys -t "agents:$WINDOW" "2" 2>/dev/null
    sleep 1
    tmux send-keys -t "agents:$WINDOW" Enter 2>/dev/null
  done
) &

echo "[channel-start] '$NAME' running"
