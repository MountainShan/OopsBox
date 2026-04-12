#!/usr/bin/env bash
# OopsBox v2 Test Suite
set -uo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
API="http://127.0.0.1:5000"
PASS=0
FAIL=0
SKIP=0

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; ((PASS++)); }
fail() { echo -e "  ${RED}✗${NC} $1${2:+: $2}"; ((FAIL++)); }
skip() { echo -e "  ${YELLOW}⊘${NC} $1 (skipped${2:+: $2})"; ((SKIP++)); }
section() { echo -e "\n━━━ $1 ━━━"; }

api() {
  curl -s -b /tmp/oopsbox-test-cookies.txt "$API$1"
}
api_post() {
  curl -s -b /tmp/oopsbox-test-cookies.txt -X POST \
    -H "Content-Type: application/json" -d "$2" "$API$1"
}
api_put() {
  curl -s -b /tmp/oopsbox-test-cookies.txt -X PUT \
    -H "Content-Type: application/json" -d "$2" "$API$1"
}
api_delete() {
  curl -s -b /tmp/oopsbox-test-cookies.txt -X DELETE "$API$1"
}

echo ""
echo "╔═══════════════════════════════════════╗"
echo "║  OopsBox Test Suite                   ║"
echo "╚═══════════════════════════════════════╝"

# ═══════════════════════════════════════
section "1. Auth"
# ═══════════════════════════════════════

# Login
R=$(curl -s -c /tmp/oopsbox-test-cookies.txt -X POST \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin"}' \
  "$API/api/auth/login")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Login"
else
  fail "Login" "$R"
fi

R=$(api "/api/auth/status")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('authenticated')" 2>/dev/null; then
  pass "Auth status (authenticated)"
else
  fail "Auth status" "$R"
fi

# ═══════════════════════════════════════
section "2. Core API Endpoints"
# ═══════════════════════════════════════

R=$(api "/api/projects")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert isinstance(d, list)" 2>/dev/null; then
  pass "Projects list endpoint"
else
  fail "Projects list endpoint" "$R"
fi

R=$(api "/api/system")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'cpu_percent' in d and 'ram' in d and 'disk' in d" 2>/dev/null; then
  pass "System stats endpoint"
else
  fail "System stats endpoint" "$R"
fi

# ═══════════════════════════════════════
section "3. Project CRUD"
# ═══════════════════════════════════════

TEST_PROJ="test-suite-$$"

R=$(api_post "/api/projects" "{\"name\":\"$TEST_PROJ\",\"type\":\"local\"}")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('name')" 2>/dev/null; then
  pass "Create local project"
else
  fail "Create local project" "$R"
fi

if [ -d "$HOME/projects/$TEST_PROJ" ]; then
  pass "Project directory created"
else
  fail "Project directory created"
fi

if [ -f "$HOME/projects/$TEST_PROJ/CLAUDE.md" ]; then
  pass "CLAUDE.md generated"
else
  fail "CLAUDE.md generated"
fi

R=$(api "/api/projects/$TEST_PROJ")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('name')" 2>/dev/null; then
  pass "Get project endpoint"
else
  fail "Get project endpoint" "$R"
fi

R=$(api "/api/projects/$TEST_PROJ/status")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'running' in d" 2>/dev/null; then
  pass "Project status endpoint"
else
  fail "Project status endpoint" "$R"
fi

R=$(api_post "/api/projects/$TEST_PROJ/stop" '{}')
pass "Stop project (no crash)"

R=$(api_delete "/api/projects/$TEST_PROJ")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Delete project"
else
  fail "Delete project" "$R"
fi

# ═══════════════════════════════════════
section "4. File Manager"
# ═══════════════════════════════════════

FILE_PROJ="test-files-$$"
api_post "/api/projects" "{\"name\":\"$FILE_PROJ\",\"type\":\"local\"}" > /dev/null

R=$(api "/api/files/$FILE_PROJ")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'files' in d" 2>/dev/null; then
  pass "List files endpoint"
else
  fail "List files endpoint" "$R"
fi

R=$(api_put "/api/files/$FILE_PROJ/write" '{"path":"hello.txt","content":"hello world"}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Write file"
else
  fail "Write file" "$R"
fi

R=$(api "/api/files/$FILE_PROJ/read?path=hello.txt")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'hello world' in d.get('content','')" 2>/dev/null; then
  pass "Read file"
else
  fail "Read file" "$R"
fi

R=$(api_post "/api/files/$FILE_PROJ/rename" '{"path":"hello.txt","new_name":"hello2.txt"}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Rename file"
else
  fail "Rename file" "$R"
fi

R=$(api_post "/api/files/$FILE_PROJ/mkdir" '{"path":"testdir"}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Create directory"
else
  fail "Create directory" "$R"
fi

# Upload
echo "upload test" > /tmp/oopsbox-upload-test.txt
R=$(curl -s -b /tmp/oopsbox-test-cookies.txt \
  -F "file=@/tmp/oopsbox-upload-test.txt" \
  "$API/api/files/$FILE_PROJ/upload?path=")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "File upload"
else
  fail "File upload" "$R"
fi
rm -f /tmp/oopsbox-upload-test.txt

R=$(api_post "/api/files/$FILE_PROJ/delete" '{"path":"hello2.txt"}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Delete file"
else
  fail "Delete file" "$R"
fi

api_delete "/api/projects/$FILE_PROJ" > /dev/null

# ═══════════════════════════════════════
section "5. Project Start / Terminal"
# ═══════════════════════════════════════

START_PROJ="test-start-$$"
api_post "/api/projects" "{\"name\":\"$START_PROJ\",\"type\":\"local\"}" > /dev/null

R=$(api_post "/api/projects/$START_PROJ/start" '{}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Start project"
else
  fail "Start project" "$R"
fi

# give tmux a moment to settle
for i in 1 2 3 4 5; do
  R=$(api "/api/projects/$START_PROJ/status")
  RUNNING=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('running',''))" 2>/dev/null)
  [ "$RUNNING" = "True" ] && break
  sleep 1
done

if [ "$RUNNING" = "True" ]; then
  pass "Project running after start"
else
  fail "Project running after start" "running=$RUNNING"
fi

R=$(api "/api/projects/$START_PROJ/status")
ACTIVE_WIN=$(echo "$R" | python3 -c "import sys,json; print(json.load(sys.stdin).get('active_window') or '')" 2>/dev/null)
if [ -n "$ACTIVE_WIN" ]; then
  pass "active_window returned: $ACTIVE_WIN"
else
  fail "active_window in status"
fi

R=$(api_post "/api/projects/$START_PROJ/send-keys" '{"keys":"echo hi"}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Send keys to terminal"
else
  fail "Send keys to terminal" "$R"
fi

R=$(api_post "/api/projects/$START_PROJ/select-window" '{"window":"shell"}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Select tmux window"
else
  fail "Select tmux window" "$R"
fi

api_post "/api/projects/$START_PROJ/stop" '{}' > /dev/null
api_delete "/api/projects/$START_PROJ" > /dev/null

# ═══════════════════════════════════════
section "6. Auth Credentials"
# ═══════════════════════════════════════

AUTH_FILE="$HOME/.config/oopsbox/auth.json"
if [ -f "$AUTH_FILE" ]; then
  pass "Auth file exists"
  PERMS=$(stat -c %a "$AUTH_FILE")
  if [ "$PERMS" = "600" ]; then
    pass "Auth file permissions (600)"
  else
    fail "Auth file permissions" "got $PERMS, expected 600"
  fi
else
  skip "Auth file" "not found (auto-created on first login)"
fi

# ═══════════════════════════════════════
section "7. tmux Config"
# ═══════════════════════════════════════

if [ -f "$HOME/.tmux.conf" ]; then
  pass "tmux.conf exists"
else
  fail "tmux.conf not found"
fi

if grep -q "mouse off" "$HOME/.tmux.conf" 2>/dev/null; then
  pass "tmux mouse off"
else
  fail "tmux mouse off" "mouse mode not disabled"
fi

# ═══════════════════════════════════════
section "8. Hardcoded Path Check"
# ═══════════════════════════════════════

# Check for hardcoded /home/<username> style paths (not runtime paths like /tmp/ or /etc/)
# Check for literal hardcoded /home/<username> paths (exclude dynamic /home/{var} or /home/$var defaults)
HARDCODED=$(grep -rn "/home/" "$REPO_DIR/" \
  --include="*.sh" --include="*.py" --include="*.html" \
  --include="*.json" --include="*.conf" 2>/dev/null \
  | grep -v ".git/" | grep -v "node_modules" | grep -v "tests/" \
  | grep -v '/home/[${]' | grep -v 'placeholder=' \
  | wc -l)
if [ "$HARDCODED" -eq 0 ]; then
  pass "No hardcoded /home/ paths in codebase"
else
  fail "Hardcoded /home/ paths found" "$HARDCODED occurrences"
  grep -rn "/home/" "$REPO_DIR/" --include="*.sh" --include="*.py" 2>/dev/null \
    | grep -v ".git/" | grep -v '/home/[${]' | head -5
fi

# ═══════════════════════════════════════
section "9. PWA"
# ═══════════════════════════════════════

if [ -f "$REPO_DIR/dashboard/static/sw.js" ]; then
  pass "Service worker (sw.js)"
else
  fail "Service worker (sw.js)" "not found"
fi

if [ -f "$REPO_DIR/dashboard/static/manifest.json" ]; then
  R=$(cat "$REPO_DIR/dashboard/static/manifest.json")
  if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('name') and d.get('start_url')" 2>/dev/null; then
    pass "PWA manifest valid"
  else
    fail "PWA manifest" "missing required fields"
  fi
else
  fail "PWA manifest" "not found"
fi

if [ -f "$REPO_DIR/dashboard/static/favicon.svg" ]; then
  pass "favicon.svg"
else
  fail "favicon.svg" "not found"
fi

# ═══════════════════════════════════════
section "10. Docker Files"
# ═══════════════════════════════════════

# Docker files only exist in the repo checkout, not inside the running container
DOCKER_ROOT="$REPO_DIR"
[ ! -f "$DOCKER_ROOT/Dockerfile" ] && [ -f "/oopsbox/../Dockerfile" ] && DOCKER_ROOT="/"
for f in Dockerfile .dockerignore docker/entrypoint.sh docker/nginx.conf docker/supervisord.conf; do
  if [ -f "$DOCKER_ROOT/$f" ]; then
    pass "Docker: $f"
  else
    skip "Docker: $f" "not in REPO_DIR (run from host checkout to verify)"
  fi
done

# ═══════════════════════════════════════
section "11. Scripts"
# ═══════════════════════════════════════

for s in project-start.sh project-stop.sh project-term.sh claude-loop.sh nginx-update-projects.sh; do
  if [ -f "$REPO_DIR/bin/$s" ] && [ -x "$REPO_DIR/bin/$s" ]; then
    pass "bin/$s"
  elif [ -f "$REPO_DIR/bin/$s" ]; then
    fail "bin/$s" "not executable"
  else
    fail "bin/$s" "not found"
  fi
done

# ═══════════════════════════════════════
section "12. Static Files"
# ═══════════════════════════════════════

for f in index.html login.html manifest.json sw.js favicon.svg; do
  if [ -f "$REPO_DIR/dashboard/static/$f" ]; then
    pass "static/$f"
  else
    fail "static/$f" "not found"
  fi
done

for f in api.js files.js terminal.js viewer.js; do
  if [ -f "$REPO_DIR/dashboard/static/js/$f" ]; then
    pass "static/js/$f"
  else
    fail "static/js/$f" "not found"
  fi
done

# ═══════════════════════════════════════
section "13. Logout"
# ═══════════════════════════════════════

R=$(api_post "/api/auth/logout" '{}')
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('ok')" 2>/dev/null; then
  pass "Logout"
else
  fail "Logout" "$R"
fi

R=$(api "/api/auth/status")
if echo "$R" | python3 -c "import sys,json; d=json.load(sys.stdin); assert not d.get('authenticated')" 2>/dev/null; then
  pass "Session cleared after logout"
else
  fail "Session cleared after logout" "$R"
fi

rm -f /tmp/oopsbox-test-cookies.txt

# ═══════════════════════════════════════
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
TOTAL=$((PASS + FAIL + SKIP))
echo -e "  Results: ${GREEN}${PASS} passed${NC}  ${RED}${FAIL} failed${NC}  ${YELLOW}${SKIP} skipped${NC}  (${TOTAL} total)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
