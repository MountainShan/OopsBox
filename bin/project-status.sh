#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
PID_DIR="/tmp/rcoder-${NAME}"

ttyd_running()  { [ -f "$PID_DIR/ttyd.pid" ] && kill -0 "$(cat $PID_DIR/ttyd.pid)" 2>/dev/null; }
agent_running() { tmux list-windows -t agents -F '#{window_name}' 2>/dev/null | grep -qx "$NAME"; }

read -r CODE_PORT TTYD_PORT < <($HOME/bin/get-project-ports.sh "$NAME")
STATUS="idle"
ttyd_running && STATUS="running"

jq -n \
  --arg  name    "$NAME" \
  --arg  status  "$STATUS" \
  --argjson cp   "$CODE_PORT" \
  --argjson tp   "$TTYD_PORT" \
  --argjson tr   "$(ttyd_running && echo true || echo false)" \
  --argjson ar   "$(agent_running && echo true || echo false)" \
  '{name:$name,status:$status,code_port:$cp,ttyd_port:$tp,
    ttyd:$tr,agent:$ar}'
