#!/bin/bash
set -e

export HOME="/oopsbox"
USER="oopsbox"

# ── API Key & Base URL ──
mkdir -p "$HOME/.config/oopsbox"
ENV_FILE="$HOME/.config/oopsbox/env"
: > "$ENV_FILE"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "export ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}'" >> "$ENV_FILE"
fi
if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
  echo "export ANTHROPIC_BASE_URL='${ANTHROPIC_BASE_URL}'" >> "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

# ── Auth setup ──
AUTH_FILE="$HOME/.config/oopsbox/auth.json"
if [ ! -f "$AUTH_FILE" ]; then
  USERNAME="${OOPSBOX_USERNAME:-admin}"
  PASSWORD="${OOPSBOX_PASSWORD:-}"

  if [ -z "$PASSWORD" ]; then
    PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    echo ""
    echo "  ====================================="
    echo "   No OOPSBOX_PASSWORD set."
    echo "   Generated login credentials:"
    echo "   Username: $USERNAME"
    echo "   Password: $PASSWORD"
    echo "  ====================================="
    echo ""
  else
    echo ""
    echo "  Login username: $USERNAME"
    echo ""
  fi

  SALT=$(python3 -c "import secrets; print(secrets.token_hex(16))")
  HASH=$(SALT="$SALT" PASSWORD="$PASSWORD" python3 -c "
import os, hashlib
salt = os.environ['SALT']
pw = os.environ['PASSWORD']
print(hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 600000).hex())
")
  cat > "$AUTH_FILE" <<EOF
{
  "username": "$USERNAME",
  "password_hash": "$HASH",
  "salt": "$SALT"
}
EOF
  chmod 600 "$AUTH_FILE"
fi

# ── Git config ──
if [ -n "${GIT_NAME:-}" ]; then
  su - $USER -c "git config --global user.name '${GIT_NAME}'"
fi
if [ -n "${GIT_EMAIL:-}" ]; then
  su - $USER -c "git config --global user.email '${GIT_EMAIL}'"
fi
if ! su - $USER -c "git config --global user.name" > /dev/null 2>&1; then
  su - $USER -c "git config --global user.name 'OopsBox'"
  su - $USER -c "git config --global user.email 'oopsbox@localhost'"
fi

# ── Fix permissions on volume mounts ──
chown -R $USER:$USER "$HOME/projects" "$HOME/.config/oopsbox" \
  "$HOME/.claude" "$HOME/channels" 2>/dev/null || true

# ── PATH in bashrc ──
grep -q 'HOME/bin' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"

# ── Claude auth status ──
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "  Claude: using API key"
elif [ -f "$HOME/.claude.json" ] && grep -q "token" "$HOME/.claude.json" 2>/dev/null; then
  echo "  Claude: OAuth authenticated"
else
  echo ""
  echo "  Claude: not authenticated"
  echo "  To authenticate, either:"
  echo "    1. Set -e ANTHROPIC_API_KEY=sk-ant-..."
  echo "    2. Run: docker exec -it -u oopsbox $(hostname) claude"
  echo "       and complete the OAuth login flow"
  echo ""
fi

# ── Start s6-overlay ──
exec /init
