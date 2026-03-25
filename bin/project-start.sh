#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="/home/mountain/projects/${NAME}"
PID_DIR="/tmp/rcoder-${NAME}"
SESSION="proj-${NAME}"
REGISTRY="/home/mountain/projects/.project-registry.json"

[ -d "$WORKDIR" ] || { echo "ERROR: project '$NAME' not found" >&2; exit 1; }
mkdir -p "$PID_DIR"

read -r CODE_PORT TTYD_PORT < <(/home/mountain/bin/get-project-ports.sh "$NAME")

# Detect backend from registry
BACKEND="local"
if [ -f "$REGISTRY" ]; then
  BACKEND=$(jq -r --arg n "$NAME" '.[$n].backend // "local"' "$REGISTRY")
fi

SKIP_PERMS="false"
if [ -f "$REGISTRY" ]; then
  SKIP_PERMS=$(jq -r --arg n "$NAME" '.[$n].skip_permissions // false' "$REGISTRY")
fi

echo "[start] $NAME — backend:${BACKEND}, skip_perms:${SKIP_PERMS}, ttyd :${TTYD_PORT}"

# tmux session
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -c "$WORKDIR"

  if [ "$BACKEND" = "ssh" ]; then
    # SSH backend: connect to remote server
    SSH_HOST=$(jq -r --arg n "$NAME" '.[$n].ssh_host' "$REGISTRY")
    SSH_PORT=$(jq -r --arg n "$NAME" '.[$n].ssh_port // 22' "$REGISTRY")
    SSH_USER=$(jq -r --arg n "$NAME" '.[$n].ssh_user' "$REGISTRY")
    SSH_AUTH=$(jq -r --arg n "$NAME" '.[$n].ssh_auth // "password"' "$REGISTRY")

    # Window 1: ai-agent (first, for chat view)
    tmux rename-window -t "$SESSION" "ai-agent"
    tmux send-keys -t "$SESSION:ai-agent" \
      "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; claude-loop.sh '$NAME' '$SKIP_PERMS'" Enter

    # Window 2: remote (SSH connection)
    tmux new-window -t "$SESSION" -n "remote" -c "$WORKDIR"
    if [ "$SSH_AUTH" = "key" ]; then
      tmux send-keys -t "$SESSION:remote" \
        "ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no -o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa ${SSH_USER}@${SSH_HOST}" Enter
    else
      if command -v sshpass >/dev/null 2>&1; then
        SSH_PASS=$(jq -r --arg n "$NAME" '.[$n].ssh_pass // ""' "$REGISTRY")
        tmux send-keys -t "$SESSION:remote" \
          "sshpass -p '${SSH_PASS}' ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no -o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa ${SSH_USER}@${SSH_HOST}" Enter
      else
        tmux send-keys -t "$SESSION:remote" \
          "ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}" Enter
      fi
    fi

    # Window 3: local terminal
    tmux new-window -t "$SESSION" -n "terminal" -c "$WORKDIR"
  else
    # Local backend: start claude agent
    tmux rename-window -t "$SESSION" "ai-agent"
    tmux send-keys -t "$SESSION:ai-agent" \
      "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; claude-loop.sh '$NAME' '$SKIP_PERMS'" Enter
    tmux new-window -t "$SESSION" -n "terminal" -c "$WORKDIR"
  fi
fi

# ttyd — load theme
THEME_CONF="/home/mountain/.config/ttyd-theme.conf"
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
    tmux attach-session -t "$SESSION:terminal" \
    > "$PID_DIR/ttyd.log" 2>&1 &
  echo $! > "$PID_DIR/ttyd.pid"
fi

/home/mountain/bin/nginx-reload-ports.sh 2>/dev/null || true
echo "[start] project '$NAME' running — ttyd :${TTYD_PORT}"
