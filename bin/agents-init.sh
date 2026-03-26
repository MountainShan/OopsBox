#!/usr/bin/env bash
# Initialize agents tmux session + system terminal on boot
set -uo pipefail

export HOME="/home/mountain"
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$HOME/.nvm/versions/node/v22.16.0/bin:/usr/local/bin:/usr/bin:/bin"
source "$HOME/.bashrc" 2>/dev/null || true

# Load API key
if [ -f "$HOME/.config/oopsbox/env" ]; then
  source "$HOME/.config/oopsbox/env"
fi

# Create agents session with system window
if ! tmux has-session -t agents 2>/dev/null; then
  tmux new-session -d -s agents -c "$HOME" -n "system"
  tmux send-keys -t "agents:system" \
    "export ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}; claude-loop.sh system" Enter
  tmux resize-window -t "agents:system" -x 300 -y 80 2>/dev/null || true
  echo "[agents-init] agents session created"
fi

# Start system terminal (ttyd)
bash "$HOME/bin/system-term.sh" start 2>&1 || true

echo "[agents-init] done"
