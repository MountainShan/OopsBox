#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="${HOME}/projects/${NAME}"
PID_DIR="/tmp/rcoder-${NAME}"
SESSION="proj-${NAME}"
REGISTRY="${HOME}/projects/.project-registry.json"
KEY_FILE="$HOME/.config/oopsbox/channel.key"

[ -d "$WORKDIR" ] || { echo "ERROR: project '$NAME' not found" >&2; exit 1; }
mkdir -p "$PID_DIR"

read -r CODE_PORT TTYD_PORT < <($HOME/bin/get-project-ports.sh "$NAME")

# Detect backend from registry
BACKEND="local"
if [ -f "$REGISTRY" ]; then
  BACKEND=$(jq -r --arg n "$NAME" '.[$n].backend // "local"' "$REGISTRY")
fi

SKIP_PERMS="false"
if [ -f "$REGISTRY" ]; then
  SKIP_PERMS=$(jq -r --arg n "$NAME" '.[$n].skip_permissions // false' "$REGISTRY")
fi

# Check isolation setting
ISOLATED="false"
if [ -f "$REGISTRY" ]; then
  ISOLATED=$(jq -r --arg n "$NAME" '.[$n].isolated // false' "$REGISTRY")
fi

# Per-project API key (encrypted in registry, overrides global key)
PROJECT_API_KEY=""
if [ -f "$REGISTRY" ]; then
  API_KEY_ENC=$(jq -r --arg n "$NAME" '.[$n].api_key_enc // ""' "$REGISTRY")
  if [ -n "$API_KEY_ENC" ] && [ "$API_KEY_ENC" != "null" ]; then
    KEY_FILE="$HOME/.config/oopsbox/channel.key"
    if [ -f "$KEY_FILE" ]; then
      KEY=$(cat "$KEY_FILE")
      PROJECT_API_KEY=$(echo "$API_KEY_ENC" | openssl enc -aes-256-cbc -a -A -d -salt -pbkdf2 -pass "pass:$KEY" 2>/dev/null || echo "")
    fi
  fi
fi
# Use per-project key if set, otherwise fall back to global (or empty for Max plan)
AGENT_API_KEY="${PROJECT_API_KEY:-${ANTHROPIC_API_KEY:-}}"

# Per-project ANTHROPIC_BASE_URL (for LiteLLM proxy)
PROJECT_BASE_URL=""
if [ -f "$REGISTRY" ]; then
  PROJECT_BASE_URL=$(jq -r --arg n "$NAME" '.[$n].anthropic_base_url // ""' "$REGISTRY")
fi
AGENT_BASE_URL="${PROJECT_BASE_URL:-${ANTHROPIC_BASE_URL:-}}"

echo "[start] $NAME — backend:${BACKEND}, skip_perms:${SKIP_PERMS}, isolated:${ISOLATED}, api_key:${PROJECT_API_KEY:+set}, base_url:${PROJECT_BASE_URL:+set}, ttyd :${TTYD_PORT}"

# AI agent → shared "agents" tmux session
if [ "$ISOLATED" = "true" ] && [ "$BACKEND" = "local" ]; then
  bash "$HOME/bin/project-start-isolated.sh" "$NAME" "$SKIP_PERMS"
else
  if ! tmux has-session -t agents 2>/dev/null; then
    tmux new-session -d -s agents -c "$HOME" -n "system"
  fi
  if ! tmux list-windows -t agents -F '#{window_name}' | grep -qx "$NAME"; then
    tmux new-window -t agents -n "$NAME" -c "$WORKDIR"
    tmux send-keys -t "agents:$NAME" \
      "export ANTHROPIC_API_KEY='${AGENT_API_KEY}'; export ANTHROPIC_BASE_URL='${AGENT_BASE_URL}'; claude-loop.sh '$NAME' '$SKIP_PERMS'" Enter
    tmux resize-window -t "agents:$NAME" -x 300 -y 80 2>/dev/null || true
  fi
fi

# No tmux needed for terminal — ttyd runs bash directly

# ttyd — load theme
THEME_CONF="${HOME}/.config/ttyd-theme.conf"
TTYD_THEME_ARGS=""
if [ -f "$THEME_CONF" ]; then
  source "$THEME_CONF"
  T="${TTYD_THEME:-dark}"
  TU=$(echo "$T" | tr '[:lower:]' '[:upper:]')
  eval "T_BG=\${${TU}_BG}; T_FG=\${${TU}_FG}; T_CURSOR=\${${TU}_CURSOR}"
  eval "T_0=\${${TU}_BLACK}; T_1=\${${TU}_RED}; T_2=\${${TU}_GREEN}; T_3=\${${TU}_YELLOW}"
  eval "T_4=\${${TU}_BLUE}; T_5=\${${TU}_MAGENTA}; T_6=\${${TU}_CYAN}; T_7=\${${TU}_WHITE}"
  eval "T_8=\${${TU}_BRIGHT_BLACK}; T_9=\${${TU}_BRIGHT_RED}; T_10=\${${TU}_BRIGHT_GREEN}; T_11=\${${TU}_BRIGHT_YELLOW}"
  eval "T_12=\${${TU}_BRIGHT_BLUE}; T_13=\${${TU}_BRIGHT_MAGENTA}; T_14=\${${TU}_BRIGHT_CYAN}; T_15=\${${TU}_BRIGHT_WHITE}"
  TTYD_THEME_ARGS="-t theme={\"background\":\"${T_BG}\",\"foreground\":\"${T_FG}\",\"cursor\":\"${T_CURSOR}\",\"black\":\"${T_0}\",\"red\":\"${T_1}\",\"green\":\"${T_2}\",\"yellow\":\"${T_3}\",\"blue\":\"${T_4}\",\"magenta\":\"${T_5}\",\"cyan\":\"${T_6}\",\"white\":\"${T_7}\",\"brightBlack\":\"${T_8}\",\"brightRed\":\"${T_9}\",\"brightGreen\":\"${T_10}\",\"brightYellow\":\"${T_11}\",\"brightBlue\":\"${T_12}\",\"brightMagenta\":\"${T_13}\",\"brightCyan\":\"${T_14}\",\"brightWhite\":\"${T_15}\"}"
fi

if [ ! -f "$PID_DIR/ttyd.pid" ] || \
   ! kill -0 "$(cat $PID_DIR/ttyd.pid)" 2>/dev/null; then

  TERM_SESSION="term-${NAME}"

  # For SSH projects, mount remote filesystem via sshfs and create terminal
  if [ "$BACKEND" = "ssh" ]; then
    SSH_HOST=$(jq -r --arg n "$NAME" '.[$n].ssh_host' "$REGISTRY")
    SSH_PORT=$(jq -r --arg n "$NAME" '.[$n].ssh_port // 22' "$REGISTRY")
    SSH_USER=$(jq -r --arg n "$NAME" '.[$n].ssh_user' "$REGISTRY")
    SSH_AUTH=$(jq -r --arg n "$NAME" '.[$n].ssh_auth // "password"' "$REGISTRY")
    SSH_OPTS="-o StrictHostKeyChecking=no -o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa"
    REMOTE_PATH=$(jq -r --arg n "$NAME" '.[$n].remote_path // "/home/'"$SSH_USER"'"' "$REGISTRY")

    # Decrypt SSH password (try encrypted field first, fall back to legacy plaintext)
    SSH_PASS=""
    SSH_PASS_ENC=$(jq -r --arg n "$NAME" '.[$n].ssh_pass_enc // ""' "$REGISTRY")
    if [ -n "$SSH_PASS_ENC" ] && [ "$SSH_PASS_ENC" != "null" ] && [ -f "$KEY_FILE" ]; then
      KEY=$(cat "$KEY_FILE")
      SSH_PASS=$(echo "$SSH_PASS_ENC" | openssl enc -aes-256-cbc -a -A -d -salt -pbkdf2 -pass "pass:$KEY" 2>/dev/null || echo "")
    fi
    if [ -z "$SSH_PASS" ]; then
      SSH_PASS=$(jq -r --arg n "$NAME" '.[$n].ssh_pass // ""' "$REGISTRY")
    fi

    # Mount remote filesystem via sshfs so all file operations go to remote
    if command -v sshfs &>/dev/null && ! mountpoint -q "$WORKDIR" 2>/dev/null; then
      SSHFS_OPTS="-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3,StrictHostKeyChecking=no,KexAlgorithms=+diffie-hellman-group14-sha1,HostKeyAlgorithms=+ssh-rsa"
      if [ "$SSH_AUTH" = "key" ]; then
        sshfs "${SSH_USER}@${SSH_HOST}:${REMOTE_PATH}" "$WORKDIR" -p "$SSH_PORT" $SSHFS_OPTS 2>/dev/null && \
          echo "[start] sshfs mounted ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH} → $WORKDIR"
      else
        echo "$SSH_PASS" | sshfs "${SSH_USER}@${SSH_HOST}:${REMOTE_PATH}" "$WORKDIR" -p "$SSH_PORT" $SSHFS_OPTS -o password_stdin 2>/dev/null && \
          echo "[start] sshfs mounted ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH} → $WORKDIR"
      fi
    fi

    # Pre-create tmux session with SSH command (remote terminal)
    if ! tmux has-session -t "$TERM_SESSION" 2>/dev/null; then
      tmux new-session -d -s "$TERM_SESSION" -c "$WORKDIR"
      if [ "$SSH_AUTH" = "key" ]; then
        tmux send-keys -t "$TERM_SESSION" "ssh -p ${SSH_PORT} ${SSH_OPTS} ${SSH_USER}@${SSH_HOST}" Enter
      else
        if command -v sshpass >/dev/null 2>&1; then
          tmux send-keys -t "$TERM_SESSION" "sshpass -p '${SSH_PASS}' ssh -p ${SSH_PORT} ${SSH_OPTS} ${SSH_USER}@${SSH_HOST}" Enter
        else
          tmux send-keys -t "$TERM_SESSION" "ssh -p ${SSH_PORT} ${SSH_OPTS} ${SSH_USER}@${SSH_HOST}" Enter
        fi
      fi
      # Add local shell window (user can switch with tmux hotkeys)
      tmux new-window -t "$TERM_SESSION" -n "local" -c "$WORKDIR"
      # Switch back to remote window as default
      tmux select-window -t "$TERM_SESSION:0"
    fi
  fi

  # For container projects, terminal connects to container shell
  if [ "$BACKEND" = "container" ] && ! tmux has-session -t "$TERM_SESSION" 2>/dev/null; then
    CT_NAME=$(jq -r --arg n "$NAME" '.[$n].container_name' "$REGISTRY")
    CT_TYPE=$(jq -r --arg n "$NAME" '.[$n].container_type // "docker"' "$REGISTRY")
    CT_USER=$(jq -r --arg n "$NAME" '.[$n].container_user // "root"' "$REGISTRY")
    CT_PATH=$(jq -r --arg n "$NAME" '.[$n].container_path // "/root"' "$REGISTRY")
    tmux new-session -d -s "$TERM_SESSION" -c "$WORKDIR"
    if [ "$CT_TYPE" = "lxc" ]; then
      tmux send-keys -t "$TERM_SESSION" "lxc exec ${CT_NAME} -- su - ${CT_USER}" Enter
    else
      tmux send-keys -t "$TERM_SESSION" "docker exec -it -u ${CT_USER} -w ${CT_PATH} ${CT_NAME} bash" Enter
    fi
    # Add local shell window
    tmux new-window -t "$TERM_SESSION" -n "local" -c "$WORKDIR"
    tmux select-window -t "$TERM_SESSION:0"
  fi

  # Pre-create tmux session for local projects so send-keys works immediately
  if [ "$BACKEND" != "ssh" ] && [ "$BACKEND" != "container" ] && ! tmux has-session -t "$TERM_SESSION" 2>/dev/null; then
    tmux new-session -d -s "$TERM_SESSION" -c "$WORKDIR"
  fi
  # Set tmux to respawn shell when it exits (e.g. Ctrl+D)
  tmux set -t "$TERM_SESSION" remain-on-exit on 2>/dev/null
  tmux set-hook -t "$TERM_SESSION" pane-died "respawn-pane -t $TERM_SESSION" 2>/dev/null
  # ttyd uses tmux new-session -A (attach if exists, create if not)
  # Wrapped in loop so if tmux session somehow dies, it recreates
  ttyd \
    --port "${TTYD_PORT}" \
    --interface 0.0.0.0 \
    --writable \
    --base-path "/proj/${NAME}/term" \
    --ping-interval 30 \
    -t fontSize=14 \
    -t fontFamily=monospace \
    -t 'enableSixel=true' \
    -t 'disableLeaveAlert=true' \
    -t 'disableReconnect=false' \
    -t 'reconnectInterval=3000' \
    ${TTYD_THEME_ARGS} \
    bash -c "while true; do tmux new-session -A -s $TERM_SESSION -c '$WORKDIR'; sleep 1; done" \
    > "$PID_DIR/ttyd.log" 2>&1 &
  echo $! > "$PID_DIR/ttyd.pid"
fi

$HOME/bin/nginx-reload-ports.sh 2>/dev/null || true
echo "[start] project '$NAME' running — ttyd :${TTYD_PORT}"
