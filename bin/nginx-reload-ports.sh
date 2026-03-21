#!/usr/bin/env bash
set -euo pipefail
STATE="/home/mountain/projects/.port-registry.json"
OUT="/etc/nginx/rcoder-ports.conf"
CODE_PROXY="/etc/nginx/rcoder-code-servers.conf"
SSL_PORT_OFFSET=100  # code-server 8100 -> SSL proxy 8200

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

# SSL proxy server blocks for each code-server port
TMP2=$(mktemp)
echo "# auto-generated code-server SSL proxies" > "$TMP2"
jq -r 'to_entries[] | "\(.key) \(.value.code_port)"' "$STATE" | \
while read -r name cp; do
  ssl_port=$((cp + SSL_PORT_OFFSET))
cat >> "$TMP2" <<BLOCK
server {
    listen ${ssl_port} ssl;
    server_name _;
    ssl_certificate     /etc/nginx/ssl/selfsigned.crt;
    ssl_certificate_key /etc/nginx/ssl/selfsigned.key;
    location / {
        proxy_pass         http://127.0.0.1:${cp};
        proxy_set_header   Host \$host:\$server_port;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_set_header   X-Forwarded-Host \$host;
        proxy_set_header   X-Forwarded-Port \$server_port;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection \$connection_upgrade;
        proxy_http_version 1.1;
        proxy_read_timeout 86400s;
        proxy_buffering    off;
    }
}
BLOCK
done
sudo cp "$TMP2" "$CODE_PROXY"
rm "$TMP2"

sudo nginx -t -q && sudo nginx -s reload
