#!/usr/bin/env bash
# Regenerate /etc/nginx/conf.d/oopsbox-projects.conf and reload nginx
set -euo pipefail

CONF_FILE="/etc/nginx/conf.d/oopsbox-projects.conf"
TMP_FILE="$(mktemp)"

# For each running project, add a location block
for PID_DIR in /tmp/oopsbox-*/; do
  [ -d "$PID_DIR" ] || continue
  NAME=$(basename "$PID_DIR" | sed 's/^oopsbox-//')
  PORT_FILE="$PID_DIR/ttyd.port"
  [ -f "$PORT_FILE" ] || continue
  PORT=$(cat "$PORT_FILE")

  cat >> "$TMP_FILE" <<EOF

location /terminal/${NAME}/ {
    proxy_pass http://127.0.0.1:${PORT}/terminal/${NAME}/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \$http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host \$host;
    proxy_read_timeout 86400s;
    proxy_buffering off;
}
EOF
done

mv "$TMP_FILE" "$CONF_FILE"
nginx -s reload 2>/dev/null || true
