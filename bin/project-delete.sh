#!/usr/bin/env bash
set -euo pipefail
NAME="$1"
WORKDIR="${HOME}/projects/${NAME}"
REGISTRY="${HOME}/projects/.project-registry.json"

[ -d "$WORKDIR" ] || { echo "ERROR: project '$NAME' not found" >&2; exit 1; }

"$HOME/bin/project-stop.sh" "$NAME" 2>/dev/null || true
rm -rf "$WORKDIR"

python3 - <<PYEOF
import json
from pathlib import Path
reg = Path("$REGISTRY")
if reg.exists():
    data = json.loads(reg.read_text())
    data.pop("$NAME", None)
    reg.write_text(json.dumps(data, indent=2))
PYEOF

echo "[delete] $NAME — done"
