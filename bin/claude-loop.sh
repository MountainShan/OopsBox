#!/usr/bin/env bash
# Auto-restart Claude, always resuming the last session
set -euo pipefail

while true; do
  claude --continue 2>/dev/null || claude
  echo ""
  echo "[claude-loop] Claude exited. Resuming in 2s... (Ctrl+C to stop)"
  sleep 2
done
