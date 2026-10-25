#!/usr/bin/env bash
# bin/deploy.sh — deploy this checkout's branch to the machine OopsBox runs on
# Usage: bin/deploy.sh [--no-restart]
#
# Run from a development clone. The commits the server lacks travel as a git
# bundle, so the server's checkout fast-forwards to exactly these commits
# (same hashes, nothing committed twice) and no push access is needed in
# either direction. On the server: the checkout must be clean, the tests
# must pass (else it goes back to where it was), then OopsBox restarts and
# must answer again.
#
# Environment:
#   OOPSBOX_HOST      ssh destination, e.g. me@my-server (required)
#   OOPSBOX_DIR       checkout there    (default side_projects/OopsBox)
#   SSHPASS           ssh password, used through sshpass when set
#   OOPSBOX_SUDO_PASS sudo password for the restart (else sudo -n)
set -euo pipefail

HOST="${OOPSBOX_HOST:?set OOPSBOX_HOST to the ssh destination, e.g. me@my-server}"
DIR="${OOPSBOX_DIR:-side_projects/OopsBox}"
RESTART=1
[ "${1:-}" = "--no-restart" ] && RESTART=0

SSH=(ssh -o StrictHostKeyChecking=accept-new)
SCP=(scp -q -o StrictHostKeyChecking=accept-new)
if [ -n "${SSHPASS:-}" ]; then
  SSH=(sshpass -e "${SSH[@]}")
  SCP=(sshpass -e "${SCP[@]}")
fi
export GIT_SSH_COMMAND="${SSH[*]}"

[ -z "$(git status --porcelain)" ] || { echo "deploy: commit or stash your changes first" >&2; exit 1; }

# what the server has now
git fetch -q "$HOST:$DIR" HEAD
BASE=$(git rev-parse FETCH_HEAD)
HEAD_=$(git rev-parse HEAD)
if [ "$BASE" = "$HEAD_" ]; then echo "deploy: server is already at $(git log --oneline -1)"; exit 0; fi
git merge-base --is-ancestor "$BASE" HEAD || {
  echo "deploy: the server has commits this branch lacks ($(git log --oneline -1 "$BASE")); merge them first" >&2; exit 1; }

BUNDLE=$(mktemp -t oopsbox).bundle
trap 'rm -f "$BUNDLE"' EXIT
git bundle create -q "$BUNDLE" "$BASE..HEAD"
"${SCP[@]}" "$BUNDLE" "$HOST:/tmp/oopsbox-deploy.bundle"
echo "deploy: $(git rev-list --count "$BASE..HEAD") commit(s) → $HOST"

"${SSH[@]}" "$HOST" OOPSBOX_SUDO_PASS="${OOPSBOX_SUDO_PASS:-}" RESTART="$RESTART" DIR="$DIR" HEAD_="$HEAD_" bash -s <<'REMOTE'
set -euo pipefail
cd "$DIR"
[ -z "$(git status --porcelain)" ] || { echo "deploy: the server checkout has uncommitted changes" >&2; git status --short >&2; exit 1; }
BEFORE=$(git rev-parse HEAD)
git fetch -q /tmp/oopsbox-deploy.bundle HEAD
git merge -q --ff-only FETCH_HEAD
rm -f /tmp/oopsbox-deploy.bundle
[ "$(git rev-parse HEAD)" = "$HEAD_" ] || { echo "deploy: server ended at $(git rev-parse HEAD), expected $HEAD_" >&2; exit 1; }
if ! out=$(.venv/bin/python -m unittest discover -s tests -t . 2>&1); then
  echo "$out" | tail -20 >&2
  git reset -q --hard "$BEFORE"
  echo "deploy: tests failed; the server stays at $(git log --oneline -1)" >&2
  exit 1
fi
echo "deploy: $(echo "$out" | grep -E '^Ran ' ) — OK"
[ "$RESTART" = 1 ] || exit 0
if [ -n "$OOPSBOX_SUDO_PASS" ]; then echo "$OOPSBOX_SUDO_PASS" | sudo -S -p '' systemctl restart oopsbox
else sudo -n systemctl restart oopsbox; fi
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/login || true)
  [ "$code" = 200 ] && { echo "deploy: OopsBox is up at $(git log --oneline -1)"; exit 0; }
  sleep 1
done
echo "deploy: OopsBox did not answer after the restart" >&2
systemctl status oopsbox --no-pager | tail -15 >&2
exit 1
REMOTE
