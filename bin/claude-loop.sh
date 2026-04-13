#!/usr/bin/env bash
# bin/claude-loop.sh — run claude --resume with auto-restart
# Usage: claude-loop.sh <project-name> [workdir]
set -euo pipefail

NAME="${1:-}"
WORKDIR="${2:-$HOME/projects/${NAME}}"

cd "$WORKDIR" 2>/dev/null || true
export CLAUDE_CODE_NO_FLICKER=1

# Pre-trust the working directory so Claude doesn't show the workspace trust dialog
_trust_dir() {
  local settings="$HOME/.claude/settings.json"
  local dir
  dir=$(realpath "$WORKDIR" 2>/dev/null || echo "$WORKDIR")
  python3 - "$settings" "$dir" <<'PY'
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
  echo "  Prefix: C-a  │  Splits: C-a | / C-a -"
  echo "  ─────────────────────────────────────"
  echo ""
}

while true; do
  _banner
  claude --resume --dangerously-skip-permissions || true
  echo ""
  echo "  Claude exited. Restarting in 2 seconds... (C-c to drop to shell)"
  sleep 2
done
