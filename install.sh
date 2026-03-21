#!/usr/bin/env bash
set -euo pipefail

USER=$(whoami)
HOME_DIR=$(eval echo ~$USER)

echo "=== RVCoder Installer ==="
echo "User: $USER"
echo "Home: $HOME_DIR"
echo ""

# ── System packages ──
echo "[1/7] Installing system packages..."
sudo apt update -qq
sudo apt install -y tmux ttyd nginx jq python3-pip python3-venv build-essential procps

# ── Dashboard venv ──
echo "[2/7] Setting up dashboard..."
sudo mkdir -p /opt/dashboard/static
sudo chown -R $USER:$USER /opt/dashboard
python3 -m venv /opt/dashboard/venv
/opt/dashboard/venv/bin/pip install -q fastapi "uvicorn[standard]" aiofiles

# Copy dashboard files
cp dashboard/main.py /opt/dashboard/
cp dashboard/static/* /opt/dashboard/static/

# ── Bin scripts ──
echo "[3/7] Installing scripts..."
mkdir -p $HOME_DIR/bin $HOME_DIR/projects
cp bin/*.sh $HOME_DIR/bin/
chmod +x $HOME_DIR/bin/*.sh
grep -q 'HOME/bin' ~/.bashrc || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc

# ── Configs ──
echo "[4/7] Configuring..."
cp config/tmux.conf $HOME_DIR/.tmux.conf
mkdir -p $HOME_DIR/.config
cp config/ttyd-theme.conf $HOME_DIR/.config/

# Claude statusline
mkdir -p $HOME_DIR/.claude
cp config/statusline-command.sh $HOME_DIR/.claude/
chmod +x $HOME_DIR/.claude/statusline-command.sh

# ── nginx ──
echo "[5/7] Configuring nginx..."
echo "# no projects" | sudo tee /etc/nginx/rcoder-ports.conf > /dev/null
echo 'set $code_port 8100;' | sudo tee -a /etc/nginx/rcoder-ports.conf > /dev/null
echo 'set $ttyd_port 9100;' | sudo tee -a /etc/nginx/rcoder-ports.conf > /dev/null
sudo cp config/nginx-site.conf /etc/nginx/sites-available/rvcoder
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/rvcoder /etc/nginx/sites-enabled/rvcoder
sudo nginx -t
sudo systemctl enable --now nginx
sudo nginx -s reload

# ── Dashboard service ──
echo "[6/7] Starting dashboard service..."
sudo tee /etc/systemd/system/dashboard.service > /dev/null <<EOF
[Unit]
Description=RVCoder Dashboard
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
echo "[7/7] Starting system terminal..."
$HOME_DIR/bin/system-term.sh start

# ── Idle check cron ──
(crontab -l 2>/dev/null | grep -v idle-check; echo "*/10 * * * * $HOME_DIR/bin/idle-check.sh >> $HOME_DIR/idle-check.log 2>&1") | crontab -

echo ""
echo "=== RVCoder installed! ==="
echo "Dashboard: http://$(hostname -I | awk '{print $1}')"
echo ""
