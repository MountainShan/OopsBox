#!/bin/bash
set -e
export HOME="/oopsbox"

echo "==> OopsBox v2 starting..."

# ── Directory setup ──
mkdir -p "$HOME/.config/oopsbox"
mkdir -p "$HOME/projects"
mkdir -p /etc/nginx/conf.d
touch /etc/nginx/conf.d/oopsbox-projects.conf

# ── Read config.yaml (if mounted) ──
CONFIG_FILE="/oopsbox/config.yaml"
get_cfg() {
  python3 -c "
import yaml, sys
f = '$CONFIG_FILE'
import os
try:
    with open(f) as fh:
        d = yaml.safe_load(fh) or {}
except:
    d = {}
keys = '$1'.split('.')
v = d
for k in keys:
    v = v.get(k, {}) if isinstance(v, dict) else {}
print(v if isinstance(v, str) else '')
" 2>/dev/null || echo ""
}

# ── Auth setup ──
AUTH_FILE="$HOME/.config/oopsbox/auth.json"
if [ ! -f "$AUTH_FILE" ]; then
  USERNAME="${OOPSBOX_USERNAME:-$(get_cfg auth.username)}"
  USERNAME="${USERNAME:-admin}"
  PASSWORD="${OOPSBOX_PASSWORD:-$(get_cfg auth.password)}"
  if [ -z "$PASSWORD" ]; then
    PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    echo ""
    echo "  ====================================="
    echo "   Generated login credentials:"
    echo "   Username: $USERNAME"
    echo "   Password: $PASSWORD"
    echo "  ====================================="
    echo ""
  fi
  SALT=$(python3 -c "import secrets; print(secrets.token_hex(16))")
  HASH=$(python3 -c "
import hashlib, sys
dk = hashlib.pbkdf2_hmac('sha256', '${PASSWORD}'.encode(), '${SALT}'.encode(), 600000)
print(dk.hex())
")
  python3 -c "
import json
from pathlib import Path
Path('$AUTH_FILE').write_text(json.dumps({
    'username': '$USERNAME',
    'salt': '$SALT',
    'password_hash': '$HASH'
}, indent=2))
Path('$AUTH_FILE').chmod(0o600)
"
fi

# ── Encryption key (for SSH passwords) ──
KEY_FILE="$HOME/.config/oopsbox/channel.key"
if [ ! -f "$KEY_FILE" ]; then
  python3 -c "import secrets; print(secrets.token_hex(32))" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
fi

# ── Git config ──
GIT_NAME="${GIT_NAME:-$(get_cfg git.name)}"
GIT_EMAIL="${GIT_EMAIL:-$(get_cfg git.email)}"
[ -n "$GIT_NAME" ] && git config --global user.name "$GIT_NAME"
[ -n "$GIT_EMAIL" ] && git config --global user.email "$GIT_EMAIL"

# ── tmux config ──
[ -f "$HOME/config/tmux.conf" ] && cp "$HOME/config/tmux.conf" "$HOME/.tmux.conf"

# ── SSL configuration ──
SSL_CERT="${SSL_CERT:-$(get_cfg ssl.cert)}"
SSL_KEY="${SSL_KEY:-$(get_cfg ssl.key)}"

if [ -n "$SSL_CERT" ] && [ -n "$SSL_KEY" ] && [ -f "$SSL_CERT" ] && [ -f "$SSL_KEY" ]; then
  echo "==> SSL enabled: $SSL_CERT"
  sed "s|SSL_CERT_PATH|${SSL_CERT}|g; s|SSL_KEY_PATH|${SSL_KEY}|g" \
    /etc/nginx/nginx-ssl.conf > /etc/nginx/nginx.conf
else
  echo "==> SSL not configured — HTTP only"
fi

# ── Ensure tmux server is ready ──
tmux start-server 2>/dev/null || true
sleep 0.5
if ! tmux has-session -t agents 2>/dev/null; then
  tmux new-session -d -s agents -n system
fi

echo "==> Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/oopsbox.conf
