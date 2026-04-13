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

_has_conversation() {
  local proj_dir
  proj_dir=$(python3 -c "
import pathlib
p = pathlib.Path('$WORKDIR').resolve()
encoded = str(p).lstrip('/').replace('/', '-')
d = pathlib.Path.home() / '.claude' / 'projects' / ('-' + encoded)
print(d)
" 2>/dev/null)
  [ -n "$(ls "$proj_dir"/*.jsonl 2>/dev/null)" ]
}

# Re-apply SHELL from project settings.json (survives Claude restarts)
_apply_shell() {
  local proj_settings="$WORKDIR/.claude/settings.json"
  local shell_path
  shell_path=$(python3 -c "
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if p.exists():
    d = json.loads(p.read_text())
    print(d.get('env', {}).get('SHELL', ''))
" "$proj_settings" 2>/dev/null)
  if [[ -n "$shell_path" && -x "$shell_path" ]]; then
    export SHELL="$shell_path"
  fi
}

while true; do
  _apply_shell
  _banner
  if _has_conversation; then
    claude --continue --dangerously-skip-permissions || true
  else
    claude --dangerously-skip-permissions || true
  fi
  echo ""
  echo "  Claude exited. Restarting in 2 seconds... (C-c to drop to shell)"
  sleep 2
done
