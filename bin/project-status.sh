#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
PID_DIR="/tmp/rcoder-${NAME}"

ttyd_running()  { [ -f "$PID_DIR/ttyd.pid" ] && kill -0 "$(cat $PID_DIR/ttyd.pid)" 2>/dev/null; }
tmux_running()  { tmux has-session -t "proj-${NAME}" 2>/dev/null; }

read -r CODE_PORT TTYD_PORT < <(/home/mountain/bin/get-project-ports.sh "$NAME")
STATUS="idle"
ttyd_running && tmux_running && STATUS="running"

jq -n \
  --arg  name    "$NAME" \
  --arg  status  "$STATUS" \
  --argjson cp   "$CODE_PORT" \
  --argjson tp   "$TTYD_PORT" \
  --argjson tr   "$(ttyd_running && echo true || echo false)" \
  --argjson tmr  "$(tmux_running && echo true || echo false)" \
  '{name:$name,status:$status,code_port:$cp,ttyd_port:$tp,
    ttyd:$tr,tmux:$tmr}'
