#!/usr/bin/env bash
# Stop projects idle for more than IDLE_MINUTES
IDLE_MINUTES=120

for PID_DIR in /tmp/rcoder-*/; do
  [ -d "$PID_DIR" ] || continue
  NAME=$(basename "$PID_DIR" | sed 's/rcoder-//')
  TTYD_PID_FILE="$PID_DIR/ttyd.pid"
  LOG="$PID_DIR/ttyd.log"

  [ -f "$TTYD_PID_FILE" ] || continue
  PID=$(cat "$TTYD_PID_FILE")
  kill -0 "$PID" 2>/dev/null || continue
  [ -f "$LOG" ] || continue

  AGE_MINS=$(( ($(date +%s) - $(stat -c %Y "$LOG")) / 60 ))
  if [ "$AGE_MINS" -gt "$IDLE_MINUTES" ]; then
    echo "[idle-check] stopping $NAME (idle ${AGE_MINS}m)"
    /home/mountain/bin/project-stop.sh "$NAME"
  fi
done
