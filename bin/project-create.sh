#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
BACKEND="${2:-local}"
WORKDIR="${HOME}/projects/${NAME}"

[ -d "$WORKDIR" ] && { echo "ERROR: project '$NAME' already exists" >&2; exit 1; }
[[ "$NAME" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo "ERROR: letters, numbers, dots, underscores, hyphens only" >&2; exit 1; }

echo "[create] initialising project: $NAME (backend: $BACKEND)"
mkdir -p "$WORKDIR"
cd "$WORKDIR"
git init -q

if [ "$BACKEND" = "ssh" ]; then
  SSH_HOST="${3:-}"
  SSH_PORT="${4:-22}"
  SSH_USER="${5:-}"
  REMOTE_PATH="${6:-/home/$SSH_USER}"

  # Detect auth type for CLAUDE.md
  SSH_AUTH="${7:-password}"

  cat > CLAUDE.md <<EOF
# Project: ${NAME}

## IMPORTANT: This is a REMOTE project
You are running on OopsBox (management host), NOT on the remote server.
ALL execution, testing, and development happens on the REMOTE server.

## Remote Server
- Host: ${SSH_HOST}
- Port: ${SSH_PORT}
- User: ${SSH_USER}
- Path: ${REMOTE_PATH}
- Auth: ${SSH_AUTH}

## Development Workflow
When working on this project, always think about:
1. **How to control the device** — use SSH commands below
2. **How to transfer code** — use scp to upload files to the remote server
3. **How to run code** — execute via SSH on the remote server
4. **How to see results** — read output from SSH command results

## SSH Access
EOF

  if [ "$SSH_AUTH" = "password" ]; then
    cat >> CLAUDE.md <<EOF

Read password from registry (NEVER hardcode it):
\`\`\`bash
SSH_PASS=\$(jq -r '.["${NAME}"].ssh_pass' ~/projects/.project-registry.json)
\`\`\`

Run commands on remote:
\`\`\`bash
SSH_PASS=\$(jq -r '.["${NAME}"].ssh_pass' ~/projects/.project-registry.json)
sshpass -p "\$SSH_PASS" ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST} '<command>'
\`\`\`

Multi-line commands:
\`\`\`bash
SSH_PASS=\$(jq -r '.["${NAME}"].ssh_pass' ~/projects/.project-registry.json)
sshpass -p "\$SSH_PASS" ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST} << 'REMOTE'
command1
command2
REMOTE
\`\`\`

Copy files:
\`\`\`bash
SSH_PASS=\$(jq -r '.["${NAME}"].ssh_pass' ~/projects/.project-registry.json)
sshpass -p "\$SSH_PASS" scp -P ${SSH_PORT} /local/file ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH}/
sshpass -p "\$SSH_PASS" scp -P ${SSH_PORT} ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH}/file ./
\`\`\`
EOF
  else
    cat >> CLAUDE.md <<EOF

Run commands on remote (key auth):
\`\`\`bash
ssh -p ${SSH_PORT} -o StrictHostKeyChecking=no ${SSH_USER}@${SSH_HOST} '<command>'
\`\`\`

Copy files:
\`\`\`bash
scp -P ${SSH_PORT} /local/file ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH}/
scp -P ${SSH_PORT} ${SSH_USER}@${SSH_HOST}:${REMOTE_PATH}/file ./
\`\`\`
EOF
  fi

  cat >> CLAUDE.md <<EOF

## Rules
- NEVER run commands locally expecting them to affect the remote server
- ALWAYS use the SSH commands above for remote operations
- NEVER hardcode passwords — always read from the registry
- Ask before installing packages, modifying configs, or restarting services
- Use the **remote** tmux window for interactive tasks (already connected)
EOF
else
  cat > CLAUDE.md <<EOF
# Project: ${NAME}

## Working directory
$(pwd)

## Agent guidelines
- Commit frequently with descriptive messages
- Keep code modular
- Ask before deleting files
EOF
fi

exec $HOME/bin/project-start.sh "$NAME"
