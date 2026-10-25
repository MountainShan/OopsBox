#!/usr/bin/env bash
# bin/codex-loop.sh — run codex with auto-restart
# Usage: codex-loop.sh <project-name> [workdir]
set -euo pipefail

NAME="${1:-}"
WORKDIR="${2:-$HOME/projects/${NAME}}"

# Resolve symlinks before anything else. ~/projects may be a link, and every
# agent records its session against the real path it ends up in -- so a script
# that keeps the unresolved path computes a different key, never finds the
# previous conversation, and silently starts a new one on every restart.
WORKDIR=$(realpath "$WORKDIR" 2>/dev/null || echo "$WORKDIR")

cd "$WORKDIR" 2>/dev/null || true

# Pre-trust the working directory so codex does not show its trust prompt.
# Codex records trust per project in ~/.codex/config.toml as:
#   [projects."/abs/path"]
#   trust_level = "trusted"
_trust_dir() {
  python3 - "$HOME/.codex/config.toml" "$WORKDIR" <<'PY'
import pathlib, sys
cfg_path, trust_dir = pathlib.Path(sys.argv[1]), sys.argv[2]
header = f'[projects."{trust_dir}"]'
text = cfg_path.read_text() if cfg_path.exists() else ""
if header in text:
    sys.exit(0)
cfg_path.parent.mkdir(parents=True, exist_ok=True)
sep = "" if text == "" or text.endswith("\n") else "\n"
cfg_path.write_text(f'{text}{sep}{header}\ntrust_level = "trusted"\n')
PY
}
_trust_dir

_banner() {
  echo ""
  echo "  >_ OopsBox · ${NAME} · codex"
  echo "  ─────────────────────────────────────"
  echo "  Prefix: C-a  │  Windows: C-a 1-4  │  Split: C-a %"
  echo "  ─────────────────────────────────────"
  echo ""
}

while true; do
  _banner
  # codex filters sessions by cwd unless --all is passed, so --last resumes
  # this project's most recent conversation, not some other project's.
  codex resume --last || codex || true
  echo ""
  echo "  Codex exited. Restarting in 2 seconds... (C-c to drop to shell)"
  sleep 2
done
