#!/usr/bin/env bash
# Start a project agent inside a Podman/Docker container for isolation.
# Called from project-start.sh when "isolated" is true in registry.
set -euo pipefail

NAME="$1"
SKIP_PERMS="${2:-false}"
WORKDIR="${HOME}/projects/${NAME}"
REGISTRY="${HOME}/projects/.project-registry.json"

# Detect runtime
if command -v podman &>/dev/null; then
  RUNTIME="podman"
elif command -v docker &>/dev/null; then
  RUNTIME="docker"
else
  echo "[isolated] WARNING: no container runtime, falling back to bare tmux"
  return 0 2>/dev/null || exit 0
fi

# Check if agent image exists
if ! $RUNTIME image exists oopsbox-agent 2>/dev/null; then
  echo "[isolated] Building agent image..."
  bash "$HOME/bin/build-agent-image.sh"
fi

CONTAINER="oopsbox-agent-${NAME}"

# Resource limits from registry
MEM_LIMIT=$(jq -r --arg n "$NAME" '.[$n].mem_limit // "4g"' "$REGISTRY" 2>/dev/null || echo "4g")
CPU_LIMIT=$(jq -r --arg n "$NAME" '.[$n].cpu_limit // "2.0"' "$REGISTRY" 2>/dev/null || echo "2.0")

# Stop existing container if any
$RUNTIME rm -f "$CONTAINER" 2>/dev/null || true

# Flags for Claude
FLAGS=""
if [ "$SKIP_PERMS" = "true" ]; then
  FLAGS="--dangerously-skip-permissions"
fi

# Ensure agents session exists
if ! tmux has-session -t agents 2>/dev/null; then
  tmux new-session -d -s agents -c "$HOME" -n "system"
fi

# Create tmux window for agent
if ! tmux list-windows -t agents -F '#{window_name}' | grep -qx "$NAME"; then
  tmux new-window -t agents -n "$NAME" -c "$WORKDIR"
  tmux resize-window -t "agents:$NAME" -x 300 -y 80 2>/dev/null || true
fi

# Start agent in container via tmux
# Bind mounts:
#   - Project dir → /workspace (read-write)
#   - .claude → /home/agent/.claude (session persistence)
#   - .gitconfig → /home/agent/.gitconfig (read-only)
RUNTIME_FLAGS=""
if [ "$RUNTIME" = "podman" ]; then
  RUNTIME_FLAGS="--userns=keep-id"
fi

tmux send-keys -t "agents:$NAME" \
  "export ANTHROPIC_API_KEY=\${ANTHROPIC_API_KEY:-}; $RUNTIME run -it --rm \
    --name $CONTAINER \
    --memory $MEM_LIMIT \
    --cpus $CPU_LIMIT \
    -v ${WORKDIR}:/workspace:Z \
    -v ${HOME}/.claude:/home/agent/.claude:Z \
    -v ${HOME}/.gitconfig:/home/agent/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    $RUNTIME_FLAGS \
    oopsbox-agent \
    claude-loop.sh '$NAME' '$SKIP_PERMS'" Enter

echo "[isolated] Agent '$NAME' started in container (mem=$MEM_LIMIT, cpus=$CPU_LIMIT)"
