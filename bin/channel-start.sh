#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WINDOW="chan-${NAME}"
REGISTRY="${HOME}/projects/.channel-registry.json"

[ -f "$REGISTRY" ] || { echo "ERROR: channel registry not found" >&2; exit 1; }

WORKDIR=$(jq -r --arg n "$NAME" '.[$n].workdir // "'"${HOME}/channels/${NAME}"'"' "$REGISTRY")

# Decrypt Telegram token
KEY_FILE="$HOME/.config/oopsbox/channel.key"
TG_TOKEN_ENC=$(jq -r --arg n "$NAME" '.[$n].telegram_token_enc // ""' "$REGISTRY")
TG_TOKEN=""
if [ -n "$TG_TOKEN_ENC" ] && [ "$TG_TOKEN_ENC" != "null" ] && [ -f "$KEY_FILE" ]; then
  KEY=$(cat "$KEY_FILE")
  TG_TOKEN=$(echo "$TG_TOKEN_ENC" | openssl enc -aes-256-cbc -a -A -d -salt -pbkdf2 -pass "pass:$KEY" 2>/dev/null || echo "")
fi
# Fallback: try legacy plaintext field
if [ -z "$TG_TOKEN" ]; then
  TG_TOKEN=$(jq -r --arg n "$NAME" '.[$n].telegram_token // ""' "$REGISTRY")
fi

# Ensure workdir exists and is pre-trusted
mkdir -p "$WORKDIR/.claude"
[ -f "$WORKDIR/.claude/settings.local.json" ] || echo '{}' > "$WORKDIR/.claude/settings.local.json"

# Set up per-channel Telegram state directory
if [ -n "$TG_TOKEN" ] && [ "$TG_TOKEN" != "null" ]; then
  CHANNEL_TG_DIR="$HOME/.claude/channels/telegram-${NAME}"
  mkdir -p "$CHANNEL_TG_DIR"
  echo "TELEGRAM_BOT_TOKEN=${TG_TOKEN}" > "$CHANNEL_TG_DIR/.env"
  chmod 600 "$CHANNEL_TG_DIR/.env"
  if [ ! -f "$CHANNEL_TG_DIR/access.json" ]; then
    echo '{"dmPolicy":"pairing","allowFrom":[],"groups":{},"pending":{}}' > "$CHANNEL_TG_DIR/access.json"
  fi
  mkdir -p "$HOME/.claude/channels/telegram"
fi

# Decrypt per-channel API key
CHANNEL_API_KEY=""
API_KEY_ENC=$(jq -r --arg n "$NAME" '.[$n].api_key_enc // ""' "$REGISTRY")
if [ -n "$API_KEY_ENC" ] && [ "$API_KEY_ENC" != "null" ] && [ -f "$KEY_FILE" ]; then
  KEY=$(cat "$KEY_FILE")
  CHANNEL_API_KEY=$(echo "$API_KEY_ENC" | openssl enc -aes-256-cbc -a -A -d -salt -pbkdf2 -pass "pass:$KEY" 2>/dev/null || echo "")
fi
AGENT_API_KEY="${CHANNEL_API_KEY:-${ANTHROPIC_API_KEY:-}}"

# Per-channel ANTHROPIC_BASE_URL (for LiteLLM proxy)
CHANNEL_BASE_URL=$(jq -r --arg n "$NAME" '.[$n].anthropic_base_url // ""' "$REGISTRY")
AGENT_BASE_URL="${CHANNEL_BASE_URL:-${ANTHROPIC_BASE_URL:-}}"

echo "[channel-start] $NAME — workdir:${WORKDIR}, api_key:${CHANNEL_API_KEY:+set}, base_url:${CHANNEL_BASE_URL:+set}"

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

CHANNEL_TG_DIR_EXPORT=""
if [ -n "${CHANNEL_TG_DIR:-}" ]; then
  CHANNEL_TG_DIR_EXPORT="export TELEGRAM_STATE_DIR='${CHANNEL_TG_DIR}';"
fi
# Use find_session_by_name to resume by name
tmux send-keys -t "agents:$WINDOW" \
  "export ANTHROPIC_API_KEY='${AGENT_API_KEY}'; export ANTHROPIC_BASE_URL='${AGENT_BASE_URL}'; ${CHANNEL_TG_DIR_EXPORT} SESSION_NAME='${WINDOW}'; while true; do SID=''; for f in \"\$HOME/.claude/sessions\"/*.json; do [ -f \"\$f\" ] || continue; sn=\$(jq -r '.name // \"\"' \"\$f\" 2>/dev/null); if [ \"\$sn\" = \"\$SESSION_NAME\" ]; then SID=\$(jq -r '.sessionId // \"\"' \"\$f\" 2>/dev/null); break; fi; done; if [ -n \"\$SID\" ] && [ \"\$SID\" != 'null' ]; then echo \"[channel] Resuming '\$SESSION_NAME': \$SID\"; claude --resume \"\$SID\" $FLAGS || claude $FLAGS; else echo \"[channel] No session for '\$SESSION_NAME', starting fresh...\"; claude $FLAGS; fi; echo '[channel] restarting in 2s...'; sleep 2; done" Enter

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
