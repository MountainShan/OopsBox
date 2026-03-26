#!/usr/bin/env bash
# Auto-restart Claude, always resuming the last session via --resume
# Trap SIGINT so Ctrl+C only kills Claude, not the loop
set -uo pipefail

SESSION_NAME="${1:-}"
SKIP_PERMS="${2:-false}"

FLAGS=""
if [ -n "$SESSION_NAME" ]; then
  FLAGS="$FLAGS -n $SESSION_NAME"
fi
if [ "$SKIP_PERMS" = "true" ]; then
  FLAGS="$FLAGS --dangerously-skip-permissions"
fi

# Session ID file — persists across restarts
SID_FILE="/tmp/claude-loop-session-${SESSION_NAME:-default}.id"

while true; do
  trap '' INT

  if [ -f "$SID_FILE" ]; then
    SID=$(cat "$SID_FILE")
    echo "[claude-loop] Resuming session: $SID"
    claude --resume "$SID" $FLAGS
    EXIT_CODE=$?
  else
    echo "[claude-loop] No saved session, starting fresh..."
    claude $FLAGS
    EXIT_CODE=$?
  fi

  trap - INT

  # After Claude exits, find and save the latest session ID for next restart
  # Claude stores sessions as JSONL files named by session ID
  WORK_DIR="${PWD}"
  HASH_DIR=$(echo "$WORK_DIR" | sed 's/[^a-zA-Z0-9]/-/g')
  SESSION_DIR="$HOME/.claude/projects/$HASH_DIR"
  if [ -d "$SESSION_DIR" ]; then
    LATEST=$(ls -t "$SESSION_DIR"/*.jsonl 2>/dev/null | head -1)
    if [ -n "$LATEST" ]; then
      # Session ID is the filename without .jsonl extension
      basename "$LATEST" .jsonl > "$SID_FILE"
      echo "[claude-loop] Saved session ID: $(cat "$SID_FILE")"
    fi
  fi

  # If resume failed, clear saved session and retry fresh
  if [ $EXIT_CODE -ne 0 ] && [ -f "$SID_FILE" ]; then
    echo "[claude-loop] Resume failed (code $EXIT_CODE), clearing saved session..."
    rm -f "$SID_FILE"
  fi

  echo ""
  echo "[claude-loop] Claude exited. Resuming in 2s..."
  sleep 2
done
