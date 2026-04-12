#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 1 ]] || { echo "Usage: $(basename "$0") <project-name> [type]" >&2; exit 1; }
NAME="$1"
TYPE="${2:-local}"

[[ "$NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || {
  echo "ERROR: invalid name '$NAME'" >&2; exit 1
}

WORKDIR="${HOME}/projects/${NAME}"
[ -d "$WORKDIR" ] && { echo "ERROR: project '$NAME' already exists" >&2; exit 1; }

mkdir -p "$WORKDIR"
cd "$WORKDIR" || { echo "ERROR: cannot cd to $WORKDIR" >&2; exit 1; }
git init -q

cat > CLAUDE.md <<EOF
# Project: ${NAME}
EOF

REGISTRY="${HOME}/projects/.project-registry.json"
python3 - <<PYEOF
import json, sys
from pathlib import Path
from datetime import datetime, timezone
reg = Path("$REGISTRY")
data = json.loads(reg.read_text()) if reg.exists() else {}
data["$NAME"] = {
    "name": "$NAME",
    "type": "$TYPE",
    "path": "$WORKDIR",
    "created_at": datetime.now(timezone.utc).isoformat()
}
reg.write_text(json.dumps(data, indent=2))
PYEOF

echo "[create] $NAME ($TYPE) — done"
