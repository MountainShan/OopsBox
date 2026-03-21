#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
STATE_FILE="/home/mountain/projects/.port-registry.json"
[ -f "$STATE_FILE" ] || echo '{}' > "$STATE_FILE"

EXISTING=$(jq -r --arg n "$NAME" '.[$n] // empty' "$STATE_FILE")
if [ -n "$EXISTING" ]; then
  echo "$EXISTING" | jq -r '"\(.code_port) \(.ttyd_port)"'
  exit 0
fi

for i in $(seq 0 99); do
  CODE_PORT=$((8100 + i))
  TTYD_PORT=$((9100 + i))
  TAKEN=$(jq --argjson cp "$CODE_PORT" '[to_entries[] | select(.value.code_port == $cp)] | length' "$STATE_FILE")
  [ "$TAKEN" = "0" ] && break
done

jq --arg n "$NAME" \
   --argjson cp "$CODE_PORT" \
   --argjson tp "$TTYD_PORT" \
   '.[$n] = {code_port: $cp, ttyd_port: $tp}' \
   "$STATE_FILE" > "$STATE_FILE.tmp" && mv "$STATE_FILE.tmp" "$STATE_FILE"

echo "$CODE_PORT $TTYD_PORT"
