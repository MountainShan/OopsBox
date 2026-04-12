#!/usr/bin/env bash
# bin/claude-loop.sh — run claude --resume with auto-restart
# Usage: claude-loop.sh <project-name> [workdir]
set -euo pipefail

NAME="${1:-}"
WORKDIR="${2:-$HOME/projects/${NAME}}"

cd "$WORKDIR" 2>/dev/null || true

_banner() {
  echo ""
  echo "  >_ OopsBox · ${NAME}"
  echo "  ─────────────────────────────────────"
  echo "  Prefix: C-a  │  Splits: C-a | / C-a -"
  echo "  ─────────────────────────────────────"
  echo ""
}

while true; do
  _banner
  claude --resume || true
  echo ""
  echo "  Claude exited. Restarting in 2 seconds... (C-c to drop to shell)"
  sleep 2
done
