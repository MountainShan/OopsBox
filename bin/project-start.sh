#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="/home/mountain/projects/${NAME}"
PID_DIR="/tmp/rcoder-${NAME}"
SESSION="proj-${NAME}"

[ -d "$WORKDIR" ] || { echo "ERROR: project '$NAME' not found" >&2; exit 1; }
mkdir -p "$PID_DIR"

read -r CODE_PORT TTYD_PORT < <(/home/mountain/bin/get-project-ports.sh "$NAME")
echo "[start] $NAME — code-server :${CODE_PORT}, ttyd :${TTYD_PORT}"

# tmux session: window 0 = claude agent, window 1 = free shell
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -c "$WORKDIR" -x 220 -y 50
  tmux rename-window -t "$SESSION" "agent"
  tmux send-keys -t "$SESSION:agent" \
    "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; claude-loop.sh" Enter
  tmux new-window -t "$SESSION" -n "shell" -c "$WORKDIR"
fi

# code-server removed — using lightweight web editor instead

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
    -t fontSize=14 \
    -t fontFamily=monospace \
    -t 'enableSixel=true' \
    -t 'disableLeaveAlert=true' \
    ${TTYD_THEME_ARGS} \
    tmux attach-session -t "$SESSION" \
    > "$PID_DIR/ttyd.log" 2>&1 &
  echo $! > "$PID_DIR/ttyd.pid"
fi

/home/mountain/bin/nginx-reload-ports.sh 2>/dev/null || true
echo "[start] project '$NAME' running — code :${CODE_PORT} term :${TTYD_PORT}"
