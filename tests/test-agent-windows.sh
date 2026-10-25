#!/usr/bin/env bash
# Multi-agent tmux window tests
set -uo pipefail

API="http://127.0.0.1:5000"
COOKIES=/tmp/oopsbox-test-cookies.txt
PASS=0
FAIL=0

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
# Use assignment, not ((PASS++)): post-increment yields the OLD value, and bash
# arithmetic exits 1 on a zero result, so ((PASS++)) at PASS=0 returns exit
# status 1 — which makes `cond && pass || fail` fire BOTH branches on the very
# first assertion, leaving FAIL=1 and the script exiting non-zero even when
# everything passed.
pass() { echo -e "  ${GREEN}✓${NC} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}✗${NC} $1${2:+: $2}"; FAIL=$((FAIL + 1)); }
section() { echo -e "\n━━━ $1 ━━━"; }

api()      { curl -s -b "$COOKIES" "$API$1"; }
api_post() { curl -s -b "$COOKIES" -X POST -H "Content-Type: application/json" ${2:+-d "$2"} "$API$1"; }
api_del()  { curl -s -b "$COOKIES" -X DELETE "$API$1"; }

PROJ="agentwin-$$"
SESSION="oopsbox-${PROJ}"

cleanup() {
  api_post "/api/projects/$PROJ/stop" >/dev/null 2>&1
  api_del "/api/projects/$PROJ" >/dev/null 2>&1
}
trap cleanup EXIT

section "Setup"
api_post "/api/projects" "{\"name\":\"$PROJ\"}" >/dev/null
[ -d "$HOME/projects/$PROJ" ] && pass "project created" || fail "project created"

api_post "/api/projects/$PROJ/start" >/dev/null
for i in $(seq 1 15); do
  sleep 1
  tmux has-session -t "$SESSION" 2>/dev/null && break
done
tmux has-session -t "$SESSION" 2>/dev/null && pass "tmux session up" || fail "tmux session up"

section "Agent windows"
windows=$(tmux list-windows -t "$SESSION" -F "#W" 2>/dev/null | sort | tr "\n" " ")
for w in claude codex kiro shell; do
  echo "$windows" | grep -qw "$w" && pass "window '$w' exists" || fail "window '$w' exists" "got: $windows"
done

section "Window switching via API"
for w in codex kiro shell claude; do
  api_post "/api/projects/$PROJ/select-window" "{\"window\":\"$w\"}" >/dev/null
  sleep 1
  got=$(api "/api/projects/$PROJ/status" | python3 -c "import json,sys; print(json.load(sys.stdin).get('active_window'))" 2>/dev/null)
  [ "$got" = "$w" ] && pass "switched to '$w'" || fail "switch to '$w'" "active_window=$got"
done

section "Per-session tmux styling (must not leak globally)"
sp=$(tmux show-options -t "$SESSION" prefix 2>/dev/null | awk '{print $2}')
[ "$sp" = "C-a" ] && pass "session prefix is C-a" || fail "session prefix" "got '$sp'"
gp=$(tmux show-options -g prefix 2>/dev/null | awk '{print $2}')
[ "$gp" != "C-a" ] && pass "global prefix untouched (is '$gp')" || fail "global prefix leaked" "global is C-a"
[ ! -e "$HOME/.tmux.conf" ] && pass "~/.tmux.conf not created" || fail "~/.tmux.conf was created"

echo -e "\nPassed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
