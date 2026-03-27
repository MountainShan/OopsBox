#!/usr/bin/env bash
# Initialize agents tmux session + system terminal on boot
set -uo pipefail

# Resolve HOME from process owner when not set (cron/systemd context)
if [ -z "${HOME:-}" ]; then
  export HOME=$(getent passwd "$(id -un)" | cut -d: -f6)
fi
source "$HOME/.bashrc" 2>/dev/null || true
# Ensure common paths are available
export PATH="$HOME/.local/bin:$HOME/.bun/bin:$PATH"
# Add node from nvm if available
[ -d "$HOME/.nvm" ] && export PATH="$(find "$HOME/.nvm/versions/node" -maxdepth 1 -type d | sort -V | tail -1)/bin:$PATH" 2>/dev/null

# Load API key
if [ -f "$HOME/.config/oopsbox/env" ]; then
  source "$HOME/.config/oopsbox/env"
fi

# Clean up stale isolation containers from previous run
for rt in podman docker; do
  if command -v $rt &>/dev/null; then
    $rt rm -f $($rt ps -a --filter "name=oopsbox-agent-" -q) 2>/dev/null || true
    break
  fi
done

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
