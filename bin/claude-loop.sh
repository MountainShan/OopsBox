#!/usr/bin/env bash
# Auto-restart Claude, resuming the last session on re-launch
set -euo pipefail

FIRST=true
while true; do
  if [ "$FIRST" = true ]; then
    claude
    FIRST=false
  else
    echo ""
    echo "[claude-loop] Claude exited. Resuming session in 2s... (Ctrl+C to stop)"
    sleep 2
    claude --continue
  fi
done
