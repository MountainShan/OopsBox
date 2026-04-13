#!/bin/bash
set -e
export HOME="/oopsbox"
export CLAUDE_CODE_NO_FLICKER=1

echo "==> OopsBox v2 starting..."

# ── Directory setup ──
mkdir -p "$HOME/.config/oopsbox"
mkdir -p "$HOME/projects"
mkdir -p /etc/nginx/conf.d
touch /etc/nginx/conf.d/oopsbox-projects.conf

# ── Read config.yaml (if mounted) ──
CONFIG_FILE="/oopsbox/config.yaml"
get_cfg() {
  OOPS_CFG_FILE="$CONFIG_FILE" OOPS_CFG_KEY="$1" python3 -c "
import yaml, os, sys
f = os.environ.get('OOPS_CFG_FILE', '')
key = os.environ.get('OOPS_CFG_KEY', '')
try:
    with open(f) as fh:
        d = yaml.safe_load(fh) or {}
except FileNotFoundError:
    d = {}
except Exception:
    d = {}
v = d
for k in key.split('.'):
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
  HASH=$(OOPS_PW="$PASSWORD" OOPS_SALT="$SALT" python3 -c "
import hashlib, os
pw = os.environ['OOPS_PW'].encode()
salt = os.environ['OOPS_SALT'].encode()
dk = hashlib.pbkdf2_hmac('sha256', pw, salt, 600000)
print(dk.hex())
")
  OOPS_USERNAME="$USERNAME" OOPS_SALT="$SALT" OOPS_HASH="$HASH" OOPS_AUTH="$AUTH_FILE" python3 -c "
import json, os
from pathlib import Path
auth_file = os.environ['OOPS_AUTH']
Path(auth_file).write_text(json.dumps({
    'username': os.environ['OOPS_USERNAME'],
    'salt': os.environ['OOPS_SALT'],
    'password_hash': os.environ['OOPS_HASH']
}, indent=2))
Path(auth_file).chmod(0o600)
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
  OOPS_CERT="$SSL_CERT" OOPS_KEY="$SSL_KEY" python3 -c "
import os
cert = os.environ['OOPS_CERT']
key = os.environ['OOPS_KEY']
with open('/etc/nginx/nginx-ssl.conf') as f:
    content = f.read()
content = content.replace('SSL_CERT_PATH', cert).replace('SSL_KEY_PATH', key)
with open('/etc/nginx/nginx.conf', 'w') as f:
    f.write(content)
"
else
  echo "==> SSL not configured — HTTP only"
fi

# ── Fix ownership so oopsbox user can access app files + mounted volumes ──
chown -R oopsbox:oopsbox /oopsbox 2>/dev/null || true

# ── Start tmux server as oopsbox (so bin/ scripts connect to same server) ──
runuser -u oopsbox -- tmux start-server 2>/dev/null || true
sleep 0.5
if ! runuser -u oopsbox -- tmux has-session -t agents 2>/dev/null; then
  runuser -u oopsbox -- tmux new-session -d -s agents -n system
fi

echo "==> Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisor/conf.d/oopsbox.conf
