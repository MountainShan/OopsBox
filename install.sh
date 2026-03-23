#!/usr/bin/env bash
set -euo pipefail

# OopsBox — I just wanted to code on iPad.
# Then this happened.

USER=$(whoami)
HOME_DIR=$(eval echo ~$USER)

echo ""
echo "  📦 OopsBox Installer"
echo "  I just wanted to code on iPad."
echo ""

# ── Login credentials ──
AUTH_FILE="$HOME_DIR/.config/oopsbox/auth.json"
if [ ! -f "$AUTH_FILE" ]; then
  echo "  Set up your dashboard login:"
  echo ""
  read -p "  Username: " OB_USER
  read -sp "  Password: " OB_PASS
  echo ""
  mkdir -p "$HOME_DIR/.config/oopsbox"
  SALT=$(python3 -c "import secrets;print(secrets.token_hex(16))")
  HASH=$(python3 -c "import hashlib;print(hashlib.sha256(('$SALT'+'$OB_PASS').encode()).hexdigest())")
  cat > "$AUTH_FILE" <<AUTHEOF
{
  "username": "$OB_USER",
  "password_hash": "$HASH",
  "salt": "$SALT"
}
AUTHEOF
  chmod 600 "$AUTH_FILE"
  echo ""
  echo "  ✓ Credentials saved (hashed)"
  echo ""
else
  echo "  ✓ Login credentials found"
  echo ""
fi

# ── Git config ──
if ! git config --global user.name > /dev/null 2>&1; then
  echo "  Set up git (used for project version control):"
  echo ""
  read -p "  Your name: " GIT_NAME
  read -p "  Your email: " GIT_EMAIL
  git config --global user.name "$GIT_NAME"
  git config --global user.email "$GIT_EMAIL"
  echo ""
  echo "  ✓ Git configured"
  echo ""
else
  echo "  ✓ Git already configured ($(git config --global user.name))"
  echo ""
fi

# ── System packages ──
echo "[1/8] installing packages (this might take a minute)..."
sudo apt update -qq
sudo apt install -y tmux ttyd nginx jq python3-pip python3-venv build-essential procps sshpass git

# ── Dashboard ──
echo "[2/8] setting up dashboard..."
sudo mkdir -p /opt/dashboard/static
sudo chown -R $USER:$USER /opt/dashboard
python3 -m venv /opt/dashboard/venv
/opt/dashboard/venv/bin/pip install -q fastapi "uvicorn[standard]" aiofiles paramiko python-multipart

cp dashboard/main.py /opt/dashboard/
cp dashboard/static/* /opt/dashboard/static/

# ── Scripts ──
echo "[3/8] installing scripts..."
mkdir -p $HOME_DIR/bin $HOME_DIR/projects
cp bin/* $HOME_DIR/bin/
chmod +x $HOME_DIR/bin/*
grep -q 'HOME/bin' ~/.bashrc || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc

# ── Configs ──
echo "[4/8] configuring tmux + terminal theme..."
cp config/tmux.conf $HOME_DIR/.tmux.conf
mkdir -p $HOME_DIR/.config
cp config/ttyd-theme.conf $HOME_DIR/.config/

# Claude statusline
mkdir -p $HOME_DIR/.claude
cp config/statusline-command.sh $HOME_DIR/.claude/
chmod +x $HOME_DIR/.claude/statusline-command.sh

# ── nginx ──
echo "[5/8] configuring nginx..."
echo '# no projects
set $code_port 8100;
set $ttyd_port 9100;' | sudo tee /etc/nginx/rcoder-ports.conf > /dev/null

# Generate nginx config
cat > /tmp/oopsbox-nginx.conf <<'NGINX'
map $http_upgrade $connection_upgrade {
    default  upgrade;
    ''       close;
}

server {
    listen 80 default_server;
    server_name _;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }

    location ~ ^/proj/(?<proj>[a-zA-Z0-9._-]+)/term(?<rest>/.*)$ {
        include /etc/nginx/rcoder-ports.conf;
        proxy_pass         http://127.0.0.1:$ttyd_port/proj/$proj/term$rest$is_args$args;
        proxy_set_header   Host              $host;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        $connection_upgrade;
        proxy_http_version 1.1;
        proxy_read_timeout 86400s;
        proxy_buffering    off;
    }

    location /system/term/ {
        proxy_pass         http://127.0.0.1:9000/system/term/;
        proxy_set_header   Host              $host;
        proxy_set_header   Upgrade           $http_upgrade;
        proxy_set_header   Connection        $connection_upgrade;
        proxy_http_version 1.1;
        proxy_read_timeout 86400s;
        proxy_buffering    off;
    }
}
NGINX
sudo cp /tmp/oopsbox-nginx.conf /etc/nginx/sites-available/oopsbox
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/oopsbox /etc/nginx/sites-enabled/oopsbox
sudo nginx -t
sudo systemctl enable --now nginx
sudo nginx -s reload

# ── Dashboard service ──
echo "[6/8] starting dashboard..."
sudo tee /etc/systemd/system/dashboard.service > /dev/null <<EOF
[Unit]
Description=OopsBox Dashboard
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/dashboard
EnvironmentFile=/etc/environment
Environment="PATH=/opt/dashboard/venv/bin:$HOME_DIR/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=/opt/dashboard/venv/bin/uvicorn main:app --host 127.0.0.1 --port 5000 --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now dashboard

# ── System terminal ──
echo "[7/8] starting system terminal..."
$HOME_DIR/bin/system-term.sh start

# ── Cron ──
echo "[8/8] setting up idle check..."
(crontab -l 2>/dev/null | grep -v idle-check; echo "*/10 * * * * $HOME_DIR/bin/idle-check.sh >> $HOME_DIR/idle-check.log 2>&1") | crontab -

echo ""
echo "  ╔═══════════════════════════════════════╗"
echo "  ║  📦 OopsBox installed!                ║"
echo "  ║                                       ║"
echo "  ║  Dashboard: http://$(hostname -I | awk '{print $1}')/"
echo "  ║                                       ║"
echo "  ║  I just wanted to code on iPad.       ║"
echo "  ║  Now there's a whole platform.        ║"
echo "  ╚═══════════════════════════════════════╝"
echo ""
