#!/usr/bin/env bash
# Auto-restart Claude, always resuming the last session by name
# Trap SIGINT so Ctrl+C only kills Claude, not the loop
set -uo pipefail

SESSION_NAME="${1:-}"
SKIP_PERMS="${2:-false}"

if [ -z "$SESSION_NAME" ]; then
  echo "[claude-loop] ERROR: session name is required" >&2
  exit 1
fi

FLAGS="-n $SESSION_NAME"
if [ "$SKIP_PERMS" = "true" ]; then
  FLAGS="$FLAGS --dangerously-skip-permissions"
fi

# Find session ID by name from ~/.claude/sessions/*.json
find_session_by_name() {
  local name="$1"
  for f in "$HOME/.claude/sessions"/*.json; do
    [ -f "$f" ] || continue
    local sname sid
    sname=$(jq -r '.name // ""' "$f" 2>/dev/null)
    if [ "$sname" = "$name" ]; then
      sid=$(jq -r '.sessionId // ""' "$f" 2>/dev/null)
      if [ -n "$sid" ] && [ "$sid" != "null" ]; then
        echo "$sid"
        return 0
      fi
    fi
  done
  return 1
}

while true; do
  trap '' INT

  SID=$(find_session_by_name "$SESSION_NAME" 2>/dev/null || echo "")

  if [ -n "$SID" ]; then
    echo "[claude-loop] Resuming session '$SESSION_NAME': $SID"
    claude --resume "$SID" $FLAGS
    EXIT_CODE=$?
  else
    echo "[claude-loop] No existing session for '$SESSION_NAME', starting fresh..."
    claude $FLAGS
    EXIT_CODE=$?
  fi

  trap - INT

  # If resume failed, start fresh next time
  if [ $EXIT_CODE -ne 0 ] && [ -n "$SID" ]; then
    echo "[claude-loop] Resume failed (code $EXIT_CODE), will start fresh next time..."
  fi

  echo ""
  echo "[claude-loop] Claude exited. Resuming in 2s..."
  sleep 2
done
