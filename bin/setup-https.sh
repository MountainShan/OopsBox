#!/usr/bin/env bash
set -euo pipefail

# Setup HTTPS for OopsBox using Tailscale built-in certs
# Requires: tailscale installed and connected

if ! command -v tailscale &>/dev/null; then
  echo "ERROR: tailscale not installed" >&2
  exit 1
fi

# Get Tailscale DNS name
DNS_NAME=$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null)
if [ -z "$DNS_NAME" ]; then
  echo "ERROR: cannot determine Tailscale DNS name" >&2
  exit 1
fi

echo "[https] Tailscale DNS: $DNS_NAME"

# Get TLS cert
CERT_DIR="/etc/nginx/ssl"
sudo mkdir -p "$CERT_DIR"
sudo tailscale cert --cert-file "$CERT_DIR/oopsbox.crt" --key-file "$CERT_DIR/oopsbox.key" "$DNS_NAME"
echo "[https] Certificate issued for $DNS_NAME"

# Update nginx to listen on 443
NGINX_CONF="/etc/nginx/sites-enabled/remote-coder"
if grep -q "listen 443" "$NGINX_CONF" 2>/dev/null; then
  echo "[https] nginx already configured for HTTPS"
else
  # Add SSL listener after the port 80 line
  sudo sed -i "/listen 80 default_server;/a\\
    listen 443 ssl default_server;\\
    ssl_certificate $CERT_DIR/oopsbox.crt;\\
    ssl_certificate_key $CERT_DIR/oopsbox.key;" "$NGINX_CONF"

  sudo nginx -t && sudo nginx -s reload
  echo "[https] nginx updated — HTTPS enabled on port 443"
fi

echo ""
echo "  HTTPS ready: https://$DNS_NAME/"
echo ""
echo "  To auto-renew certs, add to crontab:"
echo "  0 3 * * * tailscale cert --cert-file $CERT_DIR/oopsbox.crt --key-file $CERT_DIR/oopsbox.key $DNS_NAME && nginx -s reload"
echo ""
