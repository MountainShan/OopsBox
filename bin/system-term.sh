#!/usr/bin/env bash
# Start/stop system terminal on port 9000
set -euo pipefail
ACTION="${1:-start}"
PID_FILE="/tmp/rcoder-system-term.pid"
PORT=9000

case "$ACTION" in
  start)
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
      echo "[system-term] already running on :$PORT"
      exit 0
    fi
    # Load theme
    THEME_ARGS=""
    THEME_CONF="/home/mountain/.config/ttyd-theme.conf"
    if [ -f "$THEME_CONF" ]; then
      source "$THEME_CONF"
      T="${TTYD_THEME:-dark}"
      TU=$(echo "$T" | tr '[:lower:]' '[:upper:]')
      eval "T_BG=\${${TU}_BG}; T_FG=\${${TU}_FG}; T_CURSOR=\${${TU}_CURSOR}"
      eval "T_0=\${${TU}_BLACK}; T_1=\${${TU}_RED}; T_2=\${${TU}_GREEN}; T_3=\${${TU}_YELLOW}"
      eval "T_4=\${${TU}_BLUE}; T_5=\${${TU}_MAGENTA}; T_6=\${${TU}_CYAN}; T_7=\${${TU}_WHITE}"
      eval "T_8=\${${TU}_BRIGHT_BLACK}; T_9=\${${TU}_BRIGHT_RED}; T_10=\${${TU}_BRIGHT_GREEN}; T_11=\${${TU}_BRIGHT_YELLOW}"
      eval "T_12=\${${TU}_BRIGHT_BLUE}; T_13=\${${TU}_BRIGHT_MAGENTA}; T_14=\${${TU}_BRIGHT_CYAN}; T_15=\${${TU}_BRIGHT_WHITE}"
      THEME_ARGS="-t theme={\"background\":\"${T_BG}\",\"foreground\":\"${T_FG}\",\"cursor\":\"${T_CURSOR}\",\"black\":\"${T_0}\",\"red\":\"${T_1}\",\"green\":\"${T_2}\",\"yellow\":\"${T_3}\",\"blue\":\"${T_4}\",\"magenta\":\"${T_5}\",\"cyan\":\"${T_6}\",\"white\":\"${T_7}\",\"brightBlack\":\"${T_8}\",\"brightRed\":\"${T_9}\",\"brightGreen\":\"${T_10}\",\"brightYellow\":\"${T_11}\",\"brightBlue\":\"${T_12}\",\"brightMagenta\":\"${T_13}\",\"brightCyan\":\"${T_14}\",\"brightWhite\":\"${T_15}\"}"
    fi
    # Pre-create tmux session
    tmux has-session -t system 2>/dev/null || tmux new-session -d -s system -c "$HOME"
    ttyd \
      --port "$PORT" \
      --interface 0.0.0.0 \
      --writable \
      --base-path "/system/term" \
      --ping-interval 30 \
      -t fontSize=14 \
      -t fontFamily=monospace \
      -t 'enableSixel=true' \
      -t 'disableLeaveAlert=true' \
      -t 'disableReconnect=false' \
      -t 'reconnectInterval=3000' \
      ${THEME_ARGS} \
      tmux new-session -A -s system \
      > /tmp/rcoder-system-term.log 2>&1 &
    echo $! > "$PID_FILE"
    echo "[system-term] started on :$PORT"
    ;;
  stop)
    if [ -f "$PID_FILE" ]; then
      kill "$(cat $PID_FILE)" 2>/dev/null && echo "[system-term] stopped"
      rm -f "$PID_FILE"
    fi
    ;;
esac
