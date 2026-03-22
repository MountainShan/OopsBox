#!/usr/bin/env bash
set -euo pipefail
STATE="/home/mountain/projects/.port-registry.json"
OUT="/etc/nginx/rcoder-ports.conf"


if [ ! -f "$STATE" ] || [ "$(jq 'length' "$STATE")" = "0" ]; then
  printf '# no projects\nset $code_port 8100;\nset $ttyd_port 9100;\n' | sudo tee "$OUT" > /dev/null
  echo '# no code-server proxies' | sudo tee "$CODE_PROXY" > /dev/null
  sudo nginx -s reload 2>/dev/null || true
  exit 0
fi

# Port map for ttyd
TMP=$(mktemp)
echo "# auto-generated — do not edit" > "$TMP"
echo 'set $code_port 8100;' >> "$TMP"
echo 'set $ttyd_port 9100;' >> "$TMP"
jq -r 'to_entries[] | "\(.key) \(.value.code_port) \(.value.ttyd_port)"' "$STATE" | \
while read -r name cp tp; do
  echo "if (\$proj = \"${name}\") { set \$code_port ${cp}; set \$ttyd_port ${tp}; }"
done >> "$TMP"
sudo cp "$TMP" "$OUT"
rm "$TMP"

sudo nginx -t -q && sudo nginx -s reload
