#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 1 ]] || { echo "Usage: $(basename "$0") <project-name> [--shell-only]" >&2; exit 1; }
NAME="$1"
# --shell-only: plain shells for the browser terminal. Agents run in the chat
# view now; starting their tmux loops too would put two copies of one agent
# on the same conversation.
SHELL_ONLY=0
[[ "${2:-}" == "--shell-only" ]] && SHELL_ONLY=1
WORKDIR="${HOME}/projects/${NAME}"
# _global: a plain shell in the folder that holds every project (not a
# project itself — registry names start with a letter or digit)
if [[ "$NAME" == "_global" ]]; then
  WORKDIR="${HOME}/projects"
  SHELL_ONLY=1
fi
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
" 2>/dev/null || echo "local")

PROJ_TYPE=$(echo "$PROJ_META"  | sed -n '1p')
SSH_HOST=$(echo  "$PROJ_META"  | sed -n '2p')
SSH_PORT=$(echo  "$PROJ_META"  | sed -n '3p')
SSH_USER=$(echo  "$PROJ_META"  | sed -n '4p')
SSH_PASS=$(echo  "$PROJ_META"  | sed -n '5p')
REMOTE_PATH=$(echo "$PROJ_META" | sed -n '6p')

# Base SSH command (with or without password)
if [ -n "$SSH_PASS" ] && command -v sshpass &>/dev/null; then
  SSH_BASE="sshpass -p $(printf '%q' "$SSH_PASS") ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
else
  SSH_BASE="ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST}"
fi

# ── OopsBox tmux styling, applied PER SESSION ────────────────────
# Deliberately not written to ~/.tmux.conf: the user's own tmux sessions must
# keep their own prefix and appearance. Session options isolate cleanly, but
# window options do NOT — `set -t <session> -w ...` only reaches the *current*
# window, so those are applied in a loop over every window.
_style_session() {
  local s="$1" w glyph

  tmux set-option -t "$s" prefix C-a
  tmux set-option -t "$s" status on
  tmux set-option -t "$s" status-position bottom
  tmux set-option -t "$s" status-interval 5
  tmux set-option -t "$s" status-justify left
  tmux set-option -t "$s" status-style "bg=#111113,fg=#a1a1aa"
  tmux set-option -t "$s" status-left-length 28
  tmux set-option -t "$s" status-left "#[bg=#f97316,fg=#09090b,bold] #S #[bg=#111113,fg=#f97316]▶#[default] "
  tmux set-option -t "$s" status-right-length 20
  tmux set-option -t "$s" status-right "#[fg=#27272a]│ #[fg=#a1a1aa,bold]%H:%M "
  tmux set-option -t "$s" message-style         "bg=#1f1f23,fg=#fb923c,bold"
  tmux set-option -t "$s" message-command-style "bg=#1f1f23,fg=#f97316"
  tmux set-option -t "$s" pane-border-style        "fg=#27272a"
  tmux set-option -t "$s" pane-active-border-style "fg=#f97316"
  tmux set-option -t "$s" base-index 1
  tmux set-option -t "$s" renumber-windows on
  tmux set-option -t "$s" mouse off

  # Behavioural settings the browser terminal depends on, carried over from the
  # tmux.conf this branch stopped installing.
  #
  # escape-time and focus-events are deliberately absent: both are server
  # options in tmux 3.x, so setting them here would silently change every tmux
  # session on the machine, including ones outside OopsBox. Leaving them alone
  # costs a half-second escape delay and no focus reporting inside the browser
  # terminal; changing them is the user's call to make globally, not ours.
  tmux set-option -t "$s" history-limit 5000
  tmux set-option -t "$s" default-command "bash -l"
  tmux set-option -t "$s" visual-activity off
  tmux set-option -t "$s" visual-bell off
  tmux set-option -t "$s" visual-silence off

  # Windows are created before base-index is applied, so they start at 0.
  # Renumber them from base-index so the banner hint C-a 1-4 is actually true.
  tmux move-window -r -t "$s"

  # ◆ marks an agent window, › marks a plain shell
  glyph='#{?#{==:#{window_name},shell},›,◆}'
  for w in $(tmux list-windows -t "$s" -F "#{window_index}"); do
    tmux set-option -t "$s:$w" -w window-status-separator ""
    tmux set-option -t "$s:$w" -w window-status-format \
      "#[fg=#27272a] │ #[fg=#71717a]${glyph} #{window_name}#{?window_zoomed_flag, ⬡,} "
    tmux set-option -t "$s:$w" -w window-status-current-format \
      "#[fg=#27272a] │ #[fg=#f97316,bold]${glyph} #[fg=#fafafa,bold]#{window_name}#{?window_zoomed_flag, ⬡,} #[fg=#f97316]●#[default] "
    tmux set-option -t "$s:$w" -w mode-style "bg=#f97316,fg=#09090b,bold"
    # Resize to the client actually looking at this window rather than to the
    # smallest of every attached client, and track the latest attachment. These
    # two are what make the terminal fit the browser window.
    tmux set-option -t "$s:$w" -w aggressive-resize on
    tmux set-option -t "$s:$w" -w window-size latest
    tmux set-option -t "$s:$w" -w allow-passthrough on
    tmux set-option -t "$s:$w" -w mode-keys vi
    tmux set-option -t "$s:$w" -w monitor-activity off
  done
}

# Create isolated tmux session (if not already running)
if ! tmux has-session -t "$SESSION" 2>/dev/null; then

  if [ "$SHELL_ONLY" = 1 ] && [ "$PROJ_TYPE" = "ssh" ]; then
    # ── Terminal only, SSH project: remote + local shells ──
    tmux new-session -d -s "$SESSION" -n "remote" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:remote" \
      "${SSH_BASE} -t \"cd $(printf '%q' "$REMOTE_PATH") && exec \$SHELL\"" Enter
    tmux new-window -t "$SESSION" -n "local" -c "$WORKDIR"

  elif [ "$SHELL_ONLY" = 1 ]; then
    # ── Terminal only, local project: one shell ──
    tmux new-session -d -s "$SESSION" -n "shell" -c "$WORKDIR"

  elif [ "$PROJ_TYPE" = "ssh" ]; then
    # ── SSH project: claude + remote + local windows ──────
    #
    # Write a per-project bash wrapper that forwards every command to the
    # remote server.  Claude runs locally but its bash tool runs on remote.
    REMOTE_BASH="$PID_DIR/remote-bash"
    cat > "$REMOTE_BASH" << BASH_EOF
#!/bin/bash
# Forwards bash execution to remote server for project: ${NAME}
if [[ "\$1" == "-c" ]]; then
  exec ${SSH_BASE} "cd $(printf '%q' "$REMOTE_PATH") && bash -c $(printf '%q' "\${2:-}")"
elif [[ "\$#" -eq 0 ]]; then
  exec ${SSH_BASE} -t "cd $(printf '%q' "$REMOTE_PATH") && bash"
else
  exec ${SSH_BASE} -t "cd $(printf '%q' "$REMOTE_PATH") && bash \$*"
fi
BASH_EOF
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
    # ── Local project: claude + codex + kiro + shell windows ──
    tmux new-session -d -s "$SESSION" -n "claude" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:claude" \
      "exec $HOME/bin/claude-loop.sh '${NAME}' '${WORKDIR}'" Enter

    tmux new-window -t "$SESSION" -n "codex" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:codex" \
      "exec $HOME/bin/codex-loop.sh '${NAME}' '${WORKDIR}'" Enter

    tmux new-window -t "$SESSION" -n "kiro" -c "$WORKDIR"
    tmux send-keys -t "$SESSION:kiro" \
      "exec $HOME/bin/kiro-loop.sh '${NAME}' '${WORKDIR}'" Enter

    tmux new-window -t "$SESSION" -n "shell" -c "$WORKDIR"
  fi

  _style_session "$SESSION"
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
