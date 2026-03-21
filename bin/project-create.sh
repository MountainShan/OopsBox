#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="/home/mountain/projects/${NAME}"

[ -d "$WORKDIR" ] && { echo "ERROR: project '$NAME' already exists" >&2; exit 1; }
[[ "$NAME" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo "ERROR: letters, numbers, dots, underscores, hyphens only" >&2; exit 1; }

echo "[create] initialising project: $NAME"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q
cat > CLAUDE.md <<EOF
# Project: ${NAME}

## Working directory
$(pwd)

## Agent guidelines
- Commit frequently with descriptive messages
- Keep code modular
- Ask before deleting files
EOF

exec /home/mountain/bin/project-start.sh "$NAME"
