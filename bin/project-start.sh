#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 1 ]] || { echo "Usage: $(basename "$0") <project-name>" >&2; exit 1; }
NAME="$1"
WORKDIR="${HOME}/projects/${NAME}"
PID_DIR="/tmp/oopsbox-${NAME}"
SESSION="oopsbox-${NAME}"

[ -d "$WORKDIR" ] || { echo "ERROR: project '$NAME' not found" >&2; exit 1; }
mkdir -p "$PID_DIR"

# Find a free port for ttyd
TTYD_PORT=$(python3 -c "
import socket
with socket.socket() as s:
    s.bind(('', 0))
    print(s.getsockname()[1])
")

echo "[start] $NAME — ttyd port: $TTYD_PORT"
echo "$TTYD_PORT" > "$PID_DIR/ttyd.port"

# Read project metadata
PROJ_META=$(python3 -c "
import json, pathlib, sys
reg = pathlib.Path.home() / 'projects' / '.project-registry.json'
if not reg.exists():
    print('local\n\n22\n\n\n~'); sys.exit(0)
meta = json.loads(reg.read_text()).get('${NAME}', {})
print(meta.get('type', 'local'))
print(meta.get('ssh_host', ''))
print(meta.get('ssh_port', '22'))
print(meta.get('ssh_user', ''))
print(meta.get('ssh_password', ''))
print(meta.get('remote_path', '~'))
print(meta.get('ssh_key_path', ''))
" 2>/dev/null || echo "local")

PROJ_TYPE=$(echo "$PROJ_META"  | sed -n '1p')
SSH_HOST=$(echo  "$PROJ_META"  | sed -n '2p')
SSH_PORT=$(echo  "$PROJ_META"  | sed -n '3p')
SSH_USER=$(echo  "$PROJ_META"  | sed -n '4p')
SSH_PASS=$(echo  "$PROJ_META"  | sed -n '5p')
REMOTE_PATH=$(echo "$PROJ_META" | sed -n '6p')
SSH_KEY=$(echo   "$PROJ_META"  | sed -n '7p')

# Base SSH command (key > password > default)
if [ -n "$SSH_KEY" ] && [ -f "$SSH_KEY" ]; then
  SSH_BASE="ssh -p ${SSH_PORT} -i $(printf '%q' "$SSH_KEY") -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
elif [ -n "$SSH_PASS" ] && command -v sshpass &>/dev/null; then
  SSH_BASE="sshpass -p $(printf '%q' "$SSH_PASS") ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
else
  SSH_BASE="ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
fi

# Create isolated tmux session (if not already running)
if ! tmux has-session -t "$SESSION" 2>/dev/null; then

  if [ "$PROJ_TYPE" = "ssh" ]; then
    # ── SSH project: claude + remote + local windows ──────
    # Tiny per-project wrapper so SHELL can be a plain path (no args allowed in SHELL var)
    REMOTE_BASH="$PID_DIR/remote-bash"
    printf '#!/bin/bash\nexec %s %s "$@"\n' \
      "$HOME/bin/ssh-remote-bash.sh" "'${NAME}'" > "$REMOTE_BASH"
    chmod +x "$REMOTE_BASH"

    # Window 1: claude — runs locally, all bash actions go to remote via SHELL wrapper
    tmux new-session -d -s "$SESSION" -n "claude" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:claude" \
      "export SHELL=$(printf '%q' "$REMOTE_BASH"); exec $HOME/bin/claude-loop.sh '${NAME}' '${WORKDIR}'" Enter

    # Window 2: remote — interactive SSH shell on remote server
    tmux new-window -t "$SESSION" -n "remote" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:remote" \
      "${SSH_BASE} -t \"cd $(printf '%q' "$REMOTE_PATH") && exec \$SHELL\"" Enter

    # Window 3: local — local shell for local config / file management
    tmux new-window -t "$SESSION" -n "local" -c "$WORKDIR"

  else
    # ── Local project: claude + shell windows ─────────────
    tmux new-session -d -s "$SESSION" -n "claude" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:claude" \
      "exec $HOME/bin/claude-loop.sh '${NAME}' '${WORKDIR}'" Enter

    tmux new-window -t "$SESSION" -n "shell" -c "$WORKDIR"
  fi

  tmux select-window -t "$SESSION:1"
fi

# Start ttyd attached to this project's session
ttyd \
  --port "$TTYD_PORT" \
  --base-path "/terminal/${NAME}" \
  --writable \
  -t copyOnSelect=true \
  -- "$HOME/bin/project-term.sh" "$NAME" "$WORKDIR" \
  > "$PID_DIR/ttyd.log" 2>&1 &

echo $! > "$PID_DIR/ttyd.pid"

"$HOME/bin/nginx-update-projects.sh"

echo "[start] $NAME — done (ttyd pid: $(cat "$PID_DIR/ttyd.pid"))"
