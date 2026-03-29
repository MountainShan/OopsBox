# OopsBox Docker Deploy Analysis

**Date:** 2026-03-29
**Target:** Ubuntu 24.04, Docker 28.2.2, 4GB RAM, 118GB disk
**Method:** `git clone` + `docker build` + `docker run`

---

## Build Results

| Step | Status | Notes |
|---|---|---|
| `git clone` | ✅ | No issues |
| `docker build` | ✅ | Completed ~5 min, used legacy builder (no buildx) |
| `docker run` | ✅ | Container starts, all s6 services OK |

**Image size:** ~835MB (expected)

---

## Issues Found

### Issue 1: `docker exec` runs as root, tmux sessions owned by oopsbox

**Severity:** Medium
**Symptom:** `docker exec oopsbox tmux list-sessions` fails with `error connecting to /tmp/tmux-0/default`
**Root cause:** `docker exec` defaults to root (UID 0), but tmux sessions are created by the `oopsbox` user (UID 1001) and stored in `/tmp/tmux-1001/`.
**Workaround:** Use `docker exec -u oopsbox oopsbox tmux list-sessions`
**Fix needed:** Either document this or change the entrypoint to run as oopsbox user.

### Issue 2: Login API requires `username` field

**Severity:** Low
**Symptom:** `POST /api/auth/login` with only `{"password":"..."}` returns `Field required: username`
**Root cause:** The login endpoint requires both username and password, but the entrypoint generates `admin` as default username which isn't communicated clearly.
**Impact:** Users who only set `OOPSBOX_PASSWORD` won't know the username is `admin`.
**Fix needed:** Either log the username on startup, or make username optional (default to whatever is in auth.json).

### Issue 3: `auth/verify` is `internal` in nginx — cannot be called directly

**Severity:** None (by design)
**Symptom:** `GET /api/auth/verify` returns 404 when called from browser directly
**Root cause:** nginx `internal` directive means it can only be used as a subrequest, not accessed directly.
**Impact:** None — this is correct behavior. Auth works properly via the subrequest pattern.

### Issue 4: Claude Code runs but has no API key

**Severity:** Info
**Symptom:** Claude CLI starts but operates under the account configured at `/oopsbox/.claude.json` (OAuth). No `ANTHROPIC_API_KEY` was set.
**Impact:** If deploying without Claude Max subscription, need to pass `-e ANTHROPIC_API_KEY=sk-ant-...`
**Fix needed:** None — by design. User passes API key via env var or uses OAuth login inside the container.

### Issue 5: Claude OAuth login inside container

**Severity:** Medium
**Symptom:** Claude Code starts and may prompt for OAuth login inside the tmux session, which users can't easily interact with from the dashboard.
**Root cause:** Fresh container has no Claude auth. User needs to either:
  1. Pass `ANTHROPIC_API_KEY` env var (API mode)
  2. Manually `docker exec -it -u oopsbox oopsbox claude` and complete OAuth
  3. Mount existing `.claude` directory with pre-authenticated session
**Fix needed:** Document this in README. Consider adding a "Claude auth status" indicator.

### Issue 6: No warning about legacy Docker builder

**Severity:** Low
**Symptom:** `DEPRECATED: The legacy builder is deprecated and will be removed in a future release. Install the buildx component to build images with BuildKit`
**Impact:** Build still works but may break in future Docker versions.
**Fix needed:** Document `docker buildx build` as recommended, or add buildx install to instructions.

---

## What Works Correctly

| Feature | Status |
|---|---|
| s6-overlay process supervisor | ✅ All 5 services start (nginx, dashboard, agents-init, system-term, idle-check) |
| nginx reverse proxy | ✅ Auth subrequest pattern works |
| Login authentication | ✅ With username + password |
| Dashboard API (projects, system, channels) | ✅ All endpoints respond correctly |
| File upload (`/api/chat-upload`) | ✅ No-auth, 50MB limit |
| tmux agents session | ✅ Running under oopsbox user |
| Claude CLI installed | ✅ `/usr/bin/claude` |
| claude-loop.sh running | ✅ In agents:system window |
| System terminal (ttyd) | ✅ Port 9000 inside container |
| Port mapping (8080:80) | ✅ Works from host |
| Volume mounts | ✅ Persist projects, config, claude, channels |
| Entrypoint (password gen, git config) | ✅ Auto-generates password, sets git |
| Idle check (periodic) | ✅ s6 longrun service |

---

## Performance

| Metric | Value |
|---|---|
| Build time | ~5 minutes |
| Container start time | ~3 seconds |
| Memory usage (idle) | ~280MB (nginx + uvicorn + tmux + claude) |
| Disk (image) | 835MB |
| Disk (running, no projects) | ~10MB additional |

---

## Recommendations

### Must Fix
1. **Entrypoint logging:** Print username alongside password on first run
2. **README:** Add Claude auth instructions for Docker deployment

### Should Fix
3. **docker exec helper:** Add a script or alias so `docker exec oopsbox <cmd>` runs as oopsbox user by default
4. **Health check:** Add `HEALTHCHECK` to Dockerfile

### Nice to Have
5. **buildx support:** Test with `docker buildx build` and document
6. **ARM support:** Multi-arch build for ARM64 (M-series Macs, Raspberry Pi)
7. **Claude auth status API:** Endpoint to check if Claude is authenticated

---

## Test Commands

```bash
# Build
docker build -t oopsbox .

# Run
docker run -d --name oopsbox -p 8080:80 \
  -e OOPSBOX_PASSWORD=mypassword \
  -e GIT_NAME="Your Name" \
  -e GIT_EMAIL="you@example.com" \
  -v oopsbox-projects:/oopsbox/projects \
  -v oopsbox-config:/oopsbox/.config/oopsbox \
  -v oopsbox-claude:/oopsbox/.claude \
  oopsbox

# Check logs
docker logs oopsbox

# Access tmux (must use -u oopsbox)
docker exec -it -u oopsbox oopsbox tmux attach -t agents

# Login
# Username: admin (default)
# Password: whatever you set in OOPSBOX_PASSWORD
```
