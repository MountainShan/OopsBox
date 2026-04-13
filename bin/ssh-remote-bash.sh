#!/usr/bin/env bash
# bin/ssh-remote-bash.sh — used as SHELL by Claude for SSH remote projects
# Usage: ssh-remote-bash.sh <project-name>
#   Acts as a bash replacement: forwards -c commands and interactive shells to remote server.
#
# Claude sets SHELL=<path-to-this-script> so every bash invocation goes to remote.

set -euo pipefail

NAME="${1:-}"
[[ -n "$NAME" ]] || { echo "Usage: ssh-remote-bash.sh <project-name>" >&2; exit 1; }

# ── Read project metadata ────────────────────────────────
read_meta() {
  python3 - "$NAME" <<'PY'
import json, pathlib, sys
name = sys.argv[1]
reg = pathlib.Path.home() / "projects" / ".project-registry.json"
if not reg.exists():
    sys.exit(1)
meta = json.loads(reg.read_text()).get(name, {})
if meta.get("type") != "ssh":
    sys.exit(1)
print(meta.get("ssh_host", ""))
print(meta.get("ssh_port", "22"))
print(meta.get("ssh_user", ""))
print(meta.get("ssh_password", ""))
print(meta.get("remote_path", "~"))
print(meta.get("ssh_key_path", ""))
PY
}

META=$(read_meta) || { echo "ssh-remote-bash: project '$NAME' not found or not SSH" >&2; exit 1; }

SSH_HOST=$(echo "$META" | sed -n '1p')
SSH_PORT=$(echo "$META" | sed -n '2p')
SSH_USER=$(echo "$META" | sed -n '3p')
SSH_PASS=$(echo "$META" | sed -n '4p')
REMOTE_PATH=$(echo "$META" | sed -n '5p')
SSH_KEY=$(echo "$META"  | sed -n '6p')

# ── Build SSH base command ───────────────────────────────
if [[ -n "$SSH_KEY" && -f "$SSH_KEY" ]]; then
  SSH_BASE="ssh -p ${SSH_PORT} -i $(printf '%q' "$SSH_KEY") -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
elif [[ -n "$SSH_PASS" ]] && command -v sshpass &>/dev/null; then
  SSH_BASE="sshpass -p $(printf '%q' "$SSH_PASS") ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
else
  SSH_BASE="ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
fi

# ── Dispatch ─────────────────────────────────────────────
if [[ "${2:-}" == "-c" ]]; then
  # Non-interactive command: bash -c "<cmd>"
  exec $SSH_BASE "cd $(printf '%q' "$REMOTE_PATH") && bash -c $(printf '%q' "${3:-}")"
elif [[ $# -le 1 ]]; then
  # Interactive shell
  exec $SSH_BASE -t "cd $(printf '%q' "$REMOTE_PATH") && exec \$SHELL"
else
  # bash with args
  exec $SSH_BASE -t "cd $(printf '%q' "$REMOTE_PATH") && bash ${*:2}"
fi
