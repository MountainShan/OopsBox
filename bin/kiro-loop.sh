#!/usr/bin/env bash
# bin/kiro-loop.sh — run kiro-cli chat with auto-restart
# Usage: kiro-loop.sh <project-name> [workdir]
set -euo pipefail

NAME="${1:-}"
WORKDIR="${2:-$HOME/projects/${NAME}}"

# Resolve symlinks before anything else. ~/projects may be a link, and every
# agent records its session against the real path it ends up in -- so a script
# that keeps the unresolved path computes a different key, never finds the
# previous conversation, and silently starts a new one on every restart.
WORKDIR=$(realpath "$WORKDIR" 2>/dev/null || echo "$WORKDIR")
KIRO="${KIRO_BIN:-$HOME/.local/bin/kiro-cli}"

cd "$WORKDIR" 2>/dev/null || true

_banner() {
  echo ""
  echo "  >_ OopsBox · ${NAME} · kiro"
  echo "  ─────────────────────────────────────"
  echo "  Prefix: C-a  │  Windows: C-a 1-4  │  Split: C-a %"
  echo "  ─────────────────────────────────────"
  echo ""
}

while true; do
  _banner
  # --resume picks up the most recent conversation *from this directory*, and
  # starts a fresh one when there is none, so no fallback is needed.
  "$KIRO" chat --resume --trust-all-tools || true
  echo ""
  echo "  Kiro exited. Restarting in 2 seconds... (C-c to drop to shell)"
  sleep 2
done
