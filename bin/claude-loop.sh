#!/usr/bin/env bash
# Auto-restart Claude, always resuming the last session
set -euo pipefail

SESSION_NAME="${1:-}"
SKIP_PERMS="${2:-false}"

FLAGS=""
if [ -n "$SESSION_NAME" ]; then
  FLAGS="$FLAGS -n $SESSION_NAME"
fi
if [ "$SKIP_PERMS" = "true" ]; then
  FLAGS="$FLAGS --dangerously-skip-permissions"
fi

while true; do
  claude --continue $FLAGS 2>/dev/null || claude $FLAGS
  echo ""
  echo "[claude-loop] Claude exited. Resuming in 2s... (Ctrl+C to stop)"
  sleep 2
done
