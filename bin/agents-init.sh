#!/usr/bin/env bash
# Initialize agents tmux session + system terminal on boot
set -uo pipefail

# Use existing HOME if available; fallback for cron/systemd context where HOME may not be set
export HOME="${HOME:-/home/mountain}"
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

# Auto-start all registered channels
REGISTRY="$HOME/projects/.channel-registry.json"
if [ -f "$REGISTRY" ]; then
  for CHAN in $(jq -r 'keys[]' "$REGISTRY"); do
    echo "[agents-init] starting channel: $CHAN"
    bash "$HOME/bin/channel-start.sh" "$CHAN" 2>&1 || true
  done
fi

echo "[agents-init] done"
