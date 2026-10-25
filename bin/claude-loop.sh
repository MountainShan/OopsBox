#!/usr/bin/env bash
# bin/claude-loop.sh — run claude with auto-restart, continuing the last chat
# Usage: claude-loop.sh <project-name> [workdir]
set -euo pipefail

NAME="${1:-}"
WORKDIR="${2:-$HOME/projects/${NAME}}"

# Resolve symlinks before anything else. ~/projects may be a link, and every
# agent records its session against the real path it ends up in -- so a script
# that keeps the unresolved path computes a different key, never finds the
# previous conversation, and silently starts a new one on every restart.
WORKDIR=$(realpath "$WORKDIR" 2>/dev/null || echo "$WORKDIR")

cd "$WORKDIR" 2>/dev/null || true
export CLAUDE_CODE_NO_FLICKER=1

# Pre-trust the working directory so Claude doesn't show the workspace trust dialog
_trust_dir() {
  local settings="$HOME/.claude/settings.json"
  python3 - "$settings" "$WORKDIR" <<'PY'
import json, sys, pathlib
settings_path, trust_dir = pathlib.Path(sys.argv[1]), sys.argv[2]
data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
folders = data.setdefault("trustedFolders", [])
if trust_dir not in folders:
    folders.append(trust_dir)
    settings_path.write_text(json.dumps(data, indent=2))
PY
}
_trust_dir

_banner() {
  echo ""
  echo "  >_ OopsBox · ${NAME}"
  echo "  ─────────────────────────────────────"
  echo "  Prefix: C-a  │  Windows: C-a 1-4  │  Split: C-a %"
  echo "  ─────────────────────────────────────"
  echo ""
}

_has_session() {
  local key
  key=$(echo "$WORKDIR" | tr '/' '-')
  local dir="$HOME/.claude/projects/${key}"
  [ -d "$dir" ] && ls "$dir"/*.jsonl 2>/dev/null | grep -q .
}

while true; do
  _banner
  if _has_session; then
    # --continue, not --resume: bare --resume opens a picker and waits for a
    # choice, which is the wrong thing after an automatic restart and painful
    # on a tablet. This matches what codex and kiro already do -- reattach to
    # this directory's most recent conversation without asking.
    claude --continue --dangerously-skip-permissions || true
  else
    echo "  No previous session — starting fresh..."
    echo ""
    claude --dangerously-skip-permissions || true
  fi
  echo ""
  echo "  Claude exited. Restarting in 2 seconds... (C-c to drop to shell)"
  sleep 2
done
