#!/usr/bin/env bash
# Classification + memory API tests
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

api()        { curl -s -b "$COOKIES" "$API$1"; }
api_post()   { curl -s -b "$COOKIES" -X POST -H "Content-Type: application/json" -d "$2" "$API$1"; }
api_put()    { curl -s -b "$COOKIES" -X PUT  -H "Content-Type: application/json" -d "$2" "$API$1"; }
api_delete() { curl -s -b "$COOKIES" -X DELETE "$API$1"; }

PROJ="cls-test-$$"

section "Classification defaults"

api_post "/api/projects" "{\"name\":\"$PROJ\"}" > /dev/null
got=$(api "/api/projects/$PROJ" | python3 -c "import json,sys; print(json.load(sys.stdin).get('classification'))")
[ "$got" = "personal" ] && pass "new project defaults to personal" || fail "default classification" "got '$got'"

section "Memory scaffold"

if [ -f "$HOME/projects/$PROJ/.memory/STATUS.md" ] && [ -d "$HOME/projects/$PROJ/.memory/journal" ]; then
  pass "memory scaffolded at create time (filesystem check, no endpoint involved)"
else
  fail "memory scaffolded at create time" "STATUS.md or journal/ missing"
fi

files=$(api "/api/projects/$PROJ/memory" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin)['files']))")
[ "$files" = "STATUS.md" ] && pass "memory listing returns STATUS.md" || fail "memory listing" "got '$files'"

section "Classification updates"

api_put "/api/projects/$PROJ/classification" '{"classification":"work"}' > /dev/null
got=$(api "/api/projects/$PROJ" | python3 -c "import json,sys; print(json.load(sys.stdin).get('classification'))")
[ "$got" = "work" ] && pass "classification can be changed to work" || fail "update to work" "got '$got'"

code=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIES" -X PUT \
  -H "Content-Type: application/json" -d '{"classification":"bogus"}' \
  "$API/api/projects/$PROJ/classification")
[ "$code" = "400" ] && pass "invalid classification rejected with 400" || fail "reject invalid" "got HTTP $code"

section "Agent gates"

kiro=$(api "/api/projects/$PROJ/agents" | python3 -c "import json,sys; print(json.load(sys.stdin)['agents']['kiro'])")
[ "$kiro" = "available" ] && pass "work project exposes kiro" || fail "work gate" "got '$kiro'"

api_put "/api/projects/$PROJ/classification" '{"classification":"work-restricted"}' > /dev/null
claude=$(api "/api/projects/$PROJ/agents" | python3 -c "import json,sys; print(json.load(sys.stdin)['agents']['claude'])")
[ "$claude" = "blocked" ] && pass "work-restricted blocks claude" || fail "restricted gate" "got '$claude'"

api_delete "/api/projects/$PROJ" > /dev/null

echo -e "\nPassed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
