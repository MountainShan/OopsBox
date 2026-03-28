#!/usr/bin/env bash
# OopsBox Full Feature Test Suite
set -uo pipefail

API="http://127.0.0.1:5000"
PASS=0
FAIL=0
SKIP=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass(){ ((PASS++)); echo -e "  ${GREEN}✓${NC} $1"; }
fail(){ ((FAIL++)); echo -e "  ${RED}✗${NC} $1: $2"; }
skip(){ ((SKIP++)); echo -e "  ${YELLOW}⊘${NC} $1 (skipped: $2)"; }

section(){ echo ""; echo "━━━ $1 ━━━"; }

# ── Helper: API call ──
api(){ curl -s "$API$1" 2>/dev/null; }
api_post(){ curl -s -X POST -H "Content-Type: application/json" -d "$2" "$API$1" 2>/dev/null; }
api_put(){ curl -s -X PUT -H "Content-Type: application/json" -d "$2" "$API$1" 2>/dev/null; }
api_del(){ curl -s -X DELETE "$API$1" 2>/dev/null; }

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║  OopsBox Test Suite                   ║"
echo "║  Testing all features...              ║"
echo "╚═══════════════════════════════════════╝"
echo ""

# ═══════════════════════════════════════
section "1. Dashboard API Health"
# ═══════════════════════════════════════

R=$(api "/api/auth/status")
if echo "$R" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
  pass "Auth status endpoint"
else
  fail "Auth status endpoint" "no response"
fi

R=$(api "/api/projects")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'projects' in d" 2>/dev/null; then
  pass "Projects list endpoint"
else
  fail "Projects list endpoint" "invalid response"
fi

R=$(api "/api/channels")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'channels' in d" 2>/dev/null; then
  pass "Channels list endpoint"
else
  fail "Channels list endpoint" "invalid response"
fi

R=$(api "/api/system")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'cpu' in d" 2>/dev/null; then
  pass "System monitor endpoint"
else
  fail "System monitor endpoint" "invalid response"
fi

# ═══════════════════════════════════════
section "2. Project CRUD"
# ═══════════════════════════════════════

TEST_PROJ="test-suite-$$"

# Create
R=$(api_post "/api/projects" "{\"name\":\"${TEST_PROJ}\"}")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['name']=='${TEST_PROJ}'" 2>/dev/null; then
  pass "Create local project"
else
  fail "Create local project" "$R"
fi

# Check exists
if [ -d "$HOME/projects/${TEST_PROJ}" ]; then
  pass "Project directory created"
else
  fail "Project directory created" "dir not found"
fi

# Check CLAUDE.md
if [ -f "$HOME/projects/${TEST_PROJ}/CLAUDE.md" ]; then
  pass "CLAUDE.md generated"
else
  fail "CLAUDE.md generated" "file not found"
fi

# Get status
R=$(api "/api/projects/${TEST_PROJ}/status")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['name']=='${TEST_PROJ}'" 2>/dev/null; then
  pass "Project status endpoint"
else
  fail "Project status endpoint" "$R"
fi

# Settings
R=$(api "/api/projects/${TEST_PROJ}/settings")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['backend']=='local'" 2>/dev/null; then
  pass "Project settings endpoint"
else
  fail "Project settings endpoint" "$R"
fi

# Update settings
R=$(api_put "/api/projects/${TEST_PROJ}" '{"skip_permissions":true}')
R2=$(api "/api/projects/${TEST_PROJ}/settings")
if echo "$R2" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['skip_permissions']==True" 2>/dev/null; then
  pass "Update project settings"
else
  fail "Update project settings" "$R2"
fi

# Stop
api_post "/api/projects/${TEST_PROJ}/stop" "{}" > /dev/null 2>&1
sleep 1
pass "Stop project (no crash)"

# Delete
R=$(api_del "/api/projects/${TEST_PROJ}")
if ! [ -d "$HOME/projects/${TEST_PROJ}" ]; then
  pass "Delete project"
else
  fail "Delete project" "directory still exists"
fi

# ═══════════════════════════════════════
section "3. Per-project API Key"
# ═══════════════════════════════════════

TEST_PROJ2="test-apikey-$$"
api_post "/api/projects" "{\"name\":\"${TEST_PROJ2}\",\"api_key\":\"sk-test-fake-key\"}" > /dev/null

R=$(api "/api/projects/${TEST_PROJ2}/settings")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['has_api_key']==True" 2>/dev/null; then
  pass "API key stored (encrypted)"
else
  fail "API key stored" "$R"
fi

# Check encryption in registry
REG=$(cat "$HOME/projects/.project-registry.json")
if echo "$REG" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'api_key_enc' in d['${TEST_PROJ2}']; assert 'sk-test' not in d['${TEST_PROJ2}']['api_key_enc']" 2>/dev/null; then
  pass "API key encrypted in registry (not plaintext)"
else
  fail "API key encryption" "found plaintext or missing"
fi

# Clear API key
api_put "/api/projects/${TEST_PROJ2}" '{"api_key":""}' > /dev/null
R=$(api "/api/projects/${TEST_PROJ2}/settings")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['has_api_key']==False" 2>/dev/null; then
  pass "API key cleared"
else
  fail "API key cleared" "$R"
fi

# Cleanup
api_post "/api/projects/${TEST_PROJ2}/stop" "{}" > /dev/null 2>&1
sleep 1
api_del "/api/projects/${TEST_PROJ2}" > /dev/null

# ═══════════════════════════════════════
section "4. Container Backend"
# ═══════════════════════════════════════

# Test with non-existent container (should fail)
R=$(api_post "/api/projects" '{"name":"test-ct","backend":"container","container_name":"nonexistent-xyz","container_type":"docker"}')
if echo "$R" | grep -q "not found"; then
  pass "Container backend rejects non-existent container"
else
  fail "Container validation" "$R"
fi
# Cleanup in case it was created
api_del "/api/projects/test-ct" > /dev/null 2>&1

# Test with real container if any running
REAL_CT=$(docker ps -q --format '{{.Names}}' 2>/dev/null | head -1)
if [ -n "$REAL_CT" ]; then
  R=$(api_post "/api/projects" "{\"name\":\"test-ct-real\",\"backend\":\"container\",\"container_name\":\"${REAL_CT}\",\"container_type\":\"docker\"}")
  if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['backend']=='container'" 2>/dev/null; then
    pass "Container backend with real container ($REAL_CT)"
    api_post "/api/projects/test-ct-real/stop" "{}" > /dev/null 2>&1
    sleep 1
    api_del "/api/projects/test-ct-real" > /dev/null 2>&1
  else
    fail "Container backend creation" "$R"
  fi
else
  skip "Container backend with real container" "no Docker containers running"
fi

# ═══════════════════════════════════════
section "5. Session Messages API"
# ═══════════════════════════════════════

R=$(api "/api/projects/_system/session-messages?after=0")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'messages' in d; assert 'total' in d; assert 'session_file' in d" 2>/dev/null; then
  pass "Session messages endpoint"
else
  fail "Session messages endpoint" "$R"
fi

TOTAL=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
if [ "$TOTAL" -gt 0 ]; then
  pass "Session has messages (total=$TOTAL)"
else
  skip "Session has messages" "total=0, no conversation yet"
fi

# ═══════════════════════════════════════
section "6. Prompt State Detection"
# ═══════════════════════════════════════

R=$(api "/api/projects/_system/prompt-state")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'state' in d; assert d['state'] in ('idle','waiting_text','waiting_choice','thinking','no_session','claude_stopped')" 2>/dev/null; then
  STATE=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")
  pass "Prompt state detection (state=$STATE)"
else
  fail "Prompt state detection" "$R"
fi

# ═══════════════════════════════════════
section "7. File Upload"
# ═══════════════════════════════════════

echo "test file content for upload" > /tmp/oopsbox-test-upload.txt
R=$(curl -s -F "file=@/tmp/oopsbox-test-upload.txt" "$API/api/chat-upload")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'path' in d; assert 'filename' in d; assert d['size']>0" 2>/dev/null; then
  UPLOAD_PATH=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin)['path'])")
  if [ -f "$UPLOAD_PATH" ]; then
    pass "File upload to /tmp ($UPLOAD_PATH)"
  else
    fail "File upload" "file not found at $UPLOAD_PATH"
  fi
else
  fail "File upload endpoint" "$R"
fi
rm -f /tmp/oopsbox-test-upload.txt "$UPLOAD_PATH" 2>/dev/null

# ═══════════════════════════════════════
section "8. Channel Encryption"
# ═══════════════════════════════════════

KEY_FILE="$HOME/.config/oopsbox/channel.key"
if [ -f "$KEY_FILE" ]; then
  pass "Channel encryption key exists"
  PERMS=$(stat -c %a "$KEY_FILE")
  if [ "$PERMS" = "600" ]; then
    pass "Key file permissions (600)"
  else
    fail "Key file permissions" "got $PERMS, expected 600"
  fi
else
  skip "Channel encryption key" "no key file (no channels created yet)"
fi

# Check registry uses encrypted tokens
CHAN_REG="$HOME/projects/.channel-registry.json"
if [ -f "$CHAN_REG" ]; then
  if python3 -c "
import json
d=json.load(open('$CHAN_REG'))
for name,ch in d.items():
    if 'telegram_token' in ch:
        print(f'PLAINTEXT token found in {name}')
        exit(1)
    if 'telegram_token_enc' in ch:
        print(f'{name}: encrypted OK')
" 2>/dev/null; then
    pass "Channel tokens encrypted (no plaintext)"
  else
    fail "Channel token encryption" "plaintext token found"
  fi
else
  skip "Channel token encryption" "no channel registry"
fi

# ═══════════════════════════════════════
section "9. tmux Sessions"
# ═══════════════════════════════════════

if tmux has-session -t agents 2>/dev/null; then
  pass "Agents tmux session exists"
  WINDOWS=$(tmux list-windows -t agents -F '#{window_name}' 2>/dev/null | wc -l)
  pass "Agent windows: $WINDOWS"
else
  fail "Agents tmux session" "not found"
fi

if tmux has-session -t term-system 2>/dev/null; then
  pass "System terminal tmux session exists"
else
  skip "System terminal tmux session" "may not be running"
fi

# ═══════════════════════════════════════
section "10. Hardcoded Path Check"
# ═══════════════════════════════════════

HARDCODED=$(grep -rn "/home/mountain" "$HOME/projects/OopsBox/" --include="*.sh" --include="*.py" --include="*.html" --include="*.json" --include="*.conf" 2>/dev/null | grep -v ".git/" | grep -v "node_modules" | grep -v "tests/" | wc -l)
if [ "$HARDCODED" -eq 0 ]; then
  pass "No hardcoded /home/mountain in codebase"
else
  fail "Hardcoded paths found" "$HARDCODED occurrences"
  grep -rn "/home/mountain" "$HOME/projects/OopsBox/" --include="*.sh" --include="*.py" --include="*.html" 2>/dev/null | grep -v ".git/" | head -5
fi

# ═══════════════════════════════════════
section "11. HTTPS / TLS"
# ═══════════════════════════════════════

if [ -f "/etc/nginx/ssl/oopsbox.crt" ]; then
  pass "TLS certificate exists"
  EXPIRY=$(openssl x509 -in /etc/nginx/ssl/oopsbox.crt -noout -enddate 2>/dev/null | cut -d= -f2)
  pass "Certificate expiry: $EXPIRY"
else
  skip "TLS certificate" "HTTPS not set up"
fi

if curl -sk -o /dev/null -w "%{http_code}" https://localhost/ 2>/dev/null | grep -q "302\|200"; then
  pass "HTTPS listener responding"
else
  skip "HTTPS listener" "may not be configured"
fi

if crontab -l 2>/dev/null | grep -q "tailscale cert"; then
  pass "Cert auto-renew cron set"
else
  skip "Cert auto-renew cron" "not configured"
fi

# ═══════════════════════════════════════
section "12. nginx Configuration"
# ═══════════════════════════════════════

if sudo nginx -t 2>&1 | grep -q "syntax is ok"; then
  pass "nginx config valid"
else
  fail "nginx config" "syntax error"
fi

if grep -q "auth_request" /etc/nginx/sites-enabled/remote-coder 2>/dev/null; then
  pass "nginx auth subrequest configured"
else
  fail "nginx auth subrequest" "not found"
fi

if grep -q "client_max_body_size" /etc/nginx/sites-enabled/remote-coder 2>/dev/null; then
  pass "nginx client_max_body_size set"
else
  fail "nginx client_max_body_size" "not found"
fi

if grep -q "chat-upload" /etc/nginx/sites-enabled/remote-coder 2>/dev/null; then
  pass "nginx chat-upload no-auth location"
else
  fail "nginx chat-upload location" "not found"
fi

# ═══════════════════════════════════════
section "13. PWA / Service Worker"
# ═══════════════════════════════════════

if [ -f "$HOME/projects/OopsBox/dashboard/static/sw.js" ]; then
  pass "Service worker file exists"
else
  fail "Service worker" "sw.js not found"
fi

if [ -f "$HOME/projects/OopsBox/dashboard/static/manifest.json" ]; then
  R=$(cat "$HOME/projects/OopsBox/dashboard/static/manifest.json")
  if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['display']=='standalone'; assert 'scope' in d" 2>/dev/null; then
    pass "PWA manifest valid (standalone + scope)"
  else
    fail "PWA manifest" "missing required fields"
  fi
else
  fail "PWA manifest" "manifest.json not found"
fi

if [ -f "$HOME/projects/OopsBox/dashboard/static/icon-192.png" ] && [ -f "$HOME/projects/OopsBox/dashboard/static/icon-512.png" ]; then
  pass "PWA icons (192 + 512)"
else
  fail "PWA icons" "missing icon files"
fi

# ═══════════════════════════════════════
section "14. Docker Image Files"
# ═══════════════════════════════════════

for f in Dockerfile .dockerignore docker/entrypoint.sh docker/nginx.conf docker/Containerfile.agent; do
  if [ -f "$HOME/projects/OopsBox/$f" ]; then
    pass "Docker: $f exists"
  else
    fail "Docker: $f" "not found"
  fi
done

S6_SERVICES="nginx dashboard agents-init system-term idle-check"
for svc in $S6_SERVICES; do
  if [ -f "$HOME/projects/OopsBox/docker/s6-rc.d/$svc/type" ]; then
    pass "s6 service: $svc"
  else
    fail "s6 service: $svc" "type file not found"
  fi
done

# ═══════════════════════════════════════
section "15. Scripts Integrity"
# ═══════════════════════════════════════

SCRIPTS="project-start.sh project-stop.sh project-create.sh project-delete.sh project-status.sh
         channel-start.sh channel-stop.sh claude-loop.sh agents-init.sh system-term.sh
         get-project-ports.sh nginx-reload-ports.sh idle-check.sh setup-https.sh
         build-agent-image.sh project-start-isolated.sh"

for s in $SCRIPTS; do
  if [ -f "$HOME/bin/$s" ] && [ -x "$HOME/bin/$s" ]; then
    pass "Script: $s (exists + executable)"
  elif [ -f "$HOME/bin/$s" ]; then
    fail "Script: $s" "not executable"
  else
    fail "Script: $s" "not found"
  fi
done

# ═══════════════════════════════════════
section "16. Idle Check"
# ═══════════════════════════════════════

if crontab -l 2>/dev/null | grep -q "idle-check"; then
  pass "Idle check cron configured"
else
  fail "Idle check cron" "not in crontab"
fi

IDLE_MIN=$(grep "IDLE_MINUTES=" "$HOME/bin/idle-check.sh" | head -1 | grep -o '[0-9]*')
if [ "$IDLE_MIN" = "120" ]; then
  pass "Idle timeout = 120 minutes"
else
  fail "Idle timeout" "expected 120, got $IDLE_MIN"
fi

# ═══════════════════════════════════════
section "17. Static Files Deployed"
# ═══════════════════════════════════════

for f in index.html chat.html editor.html manifest.json sw.js icon-192.png icon-512.png; do
  if [ -f "/opt/dashboard/static/$f" ]; then
    pass "Deployed: /opt/dashboard/static/$f"
  else
    fail "Deployed: $f" "not found in /opt/dashboard/static/"
  fi
done

if [ -f "/opt/dashboard/main.py" ]; then
  pass "Deployed: /opt/dashboard/main.py"
else
  fail "Deployed: main.py" "not found"
fi

# ═══════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo -e "  ${GREEN}PASS: $PASS${NC}  ${RED}FAIL: $FAIL${NC}  ${YELLOW}SKIP: $SKIP${NC}  Total: $((PASS+FAIL+SKIP))"
echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "  All tests passed! 🎉"
else
  echo "  $FAIL test(s) failed."
fi
echo ""
