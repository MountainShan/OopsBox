#!/bin/bash
set -e

HOME_DIR="/home/mountain"

# ── API Key ──
mkdir -p "$HOME_DIR/.config/oopsbox"
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "export ANTHROPIC_API_KEY='${ANTHROPIC_API_KEY}'" > "$HOME_DIR/.config/oopsbox/env"
  chmod 600 "$HOME_DIR/.config/oopsbox/env"
fi

# ── Auth setup ──
AUTH_FILE="$HOME_DIR/.config/oopsbox/auth.json"
if [ ! -f "$AUTH_FILE" ]; then
  USERNAME="${OOPSBOX_USERNAME:-admin}"
  PASSWORD="${OOPSBOX_PASSWORD:-}"

  if [ -z "$PASSWORD" ]; then
    PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    echo ""
    echo "  ╔═══════════════════════════════════════════╗"
    echo "  ║  No OOPSBOX_PASSWORD set.                 ║"
    echo "  ║  Generated password: $PASSWORD"
    echo "  ║  Username: $USERNAME"
    echo "  ╚═══════════════════════════════════════════╝"
    echo ""
  fi

  SALT=$(python3 -c "import secrets; print(secrets.token_hex(16))")
  HASH=$(python3 -c "import hashlib; print(hashlib.sha256(('${SALT}'+'''${PASSWORD}''').encode()).hexdigest())")
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
  su - mountain -c "git config --global user.name '${GIT_NAME}'"
fi
if [ -n "${GIT_EMAIL:-}" ]; then
  su - mountain -c "git config --global user.email '${GIT_EMAIL}'"
fi
# Ensure git has at least a default config
if ! su - mountain -c "git config --global user.name" > /dev/null 2>&1; then
  su - mountain -c "git config --global user.name 'OopsBox'"
  su - mountain -c "git config --global user.email 'oopsbox@localhost'"
fi

# ── Fix permissions on volume mounts ──
chown -R mountain:mountain "$HOME_DIR/projects" "$HOME_DIR/.config/oopsbox" \
  "$HOME_DIR/.claude" "$HOME_DIR/channels" 2>/dev/null || true

# ── Add PATH to bashrc if missing ──
grep -q 'HOME/bin' "$HOME_DIR/.bashrc" || echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME_DIR/.bashrc"

# ── Start s6-overlay ──
exec /init
