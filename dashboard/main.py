import json
import subprocess
import os
import secrets
import time
import hashlib
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, field_validator
from typing import Optional
import re
import paramiko
import io

app = FastAPI(title="OopsBox")

# ── Auth ─────────────────────────────────────────────────────────────────────

AUTH_FILE = Path.home() / ".config" / "oopsbox" / "auth.json"
SESSIONS = {}  # token → expires_at
SESSION_TTL = 86400  # 24h

def hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt

def load_auth() -> dict:
    if AUTH_FILE.exists():
        return json.loads(AUTH_FILE.read_text())
    return None

def save_auth(username: str, password: str):
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    hashed, salt = hash_password(password)
    data = {"username": username, "password_hash": hashed, "salt": salt}
    AUTH_FILE.write_text(json.dumps(data, indent=2))
    os.chmod(str(AUTH_FILE), 0o600)

def verify_password(username: str, password: str) -> bool:
    auth = load_auth()
    if not auth:
        return False
    if username != auth.get("username"):
        return False
    hashed, _ = hash_password(password, auth.get("salt", ""))
    return hashed == auth.get("password_hash")

# Auto-migrate from ~/upw.txt if auth.json doesn't exist
_upw = Path.home() / "upw.txt"
if not AUTH_FILE.exists() and _upw.exists():
    lines = _upw.read_text().strip().split('\n')
    if len(lines) >= 2:
        save_auth(lines[0].strip(), lines[1].strip())

def verify_session(token: str) -> bool:
    if not token or token not in SESSIONS:
        return False
    if time.time() > SESSIONS[token]:
        del SESSIONS[token]
        return False
    return True

class LoginReq(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def api_login(body: LoginReq):
    if verify_password(body.username, body.password):
        token = secrets.token_hex(32)
        SESSIONS[token] = time.time() + SESSION_TTL
        resp = JSONResponse({"ok": True})
        resp.set_cookie("oopsbox_session", token, max_age=SESSION_TTL, httponly=True, samesite="lax")
        return resp
    raise HTTPException(401, "wrong username or password")

@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    token = request.cookies.get("oopsbox_session", "")
    return {"authenticated": verify_session(token)}

@app.post("/api/auth/logout")
async def api_logout(request: Request):
    token = request.cookies.get("oopsbox_session", "")
    SESSIONS.pop(token, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("oopsbox_session")
    return resp

@app.get("/api/auth/verify")
async def api_verify(request: Request):
    token = request.cookies.get("oopsbox_session", "")
    if verify_session(token):
        return JSONResponse({"ok": True}, status_code=200)
    return JSONResponse({"ok": False}, status_code=401)

@app.get("/login")
async def login_page():
    return FileResponse("/opt/dashboard/static/login.html")

PROJECTS_DIR = Path("/home/mountain/projects")
BIN_DIR      = Path("/home/mountain/bin")
REGISTRY_FILE = PROJECTS_DIR / ".project-registry.json"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)


# ── Project Registry ─────────────────────────────────────────────────────────

def load_registry() -> dict:
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text())
    return {}

def save_registry(reg: dict):
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2))
    os.chmod(str(REGISTRY_FILE), 0o600)

def get_project_meta(name: str) -> dict:
    reg = load_registry()
    return reg.get(name, {"backend": "local"})


def project_names() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in PROJECTS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def get_status(name: str) -> dict:
    meta = get_project_meta(name)
    r = run([str(BIN_DIR / "project-status.sh"), name])
    if r.returncode != 0 or not r.stdout.strip():
        status = {"name": name, "status": "idle",
                "code_port": None, "ttyd_port": None,
                "ttyd": False, "tmux": False}
    else:
        status = json.loads(r.stdout)
    status["backend"] = meta.get("backend", "local")
    if status["backend"] == "ssh":
        status["ssh_host"] = meta.get("ssh_host", "")
        status["ssh_user"] = meta.get("ssh_user", "")
    return status


# ── SFTP Helper ──────────────────────────────────────────────────────────────

def get_ssh_client(meta: dict) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    auth = {"hostname": meta["ssh_host"], "port": meta.get("ssh_port", 22),
            "username": meta["ssh_user"]}
    if meta.get("ssh_auth") == "key" and meta.get("ssh_key"):
        pkey = paramiko.RSAKey.from_private_key(io.StringIO(meta["ssh_key"]))
        auth["pkey"] = pkey
    else:
        auth["password"] = meta.get("ssh_pass", "")
    # Support legacy SSH algorithms for older devices
    auth["disabled_algorithms"] = {}
    auth["allow_agent"] = False
    auth["look_for_keys"] = False
    try:
        ssh.connect(**auth, timeout=10)
    except paramiko.SSHException:
        # Retry with legacy transport settings
        transport = paramiko.Transport((meta["ssh_host"], meta.get("ssh_port", 22)))
        transport.connect(username=meta["ssh_user"], password=meta.get("ssh_pass", ""))
        ssh._transport = transport
    return ssh

def get_sftp(name: str):
    meta = get_project_meta(name)
    if meta.get("backend") != "ssh":
        return None, None
    ssh = get_ssh_client(meta)
    return ssh, ssh.open_sftp()


# ── Project API ──────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects():
    return {"projects": [get_status(n) for n in project_names()]}


class CreateReq(BaseModel):
    name: str
    backend: str = "local"
    ssh_host: Optional[str] = None
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_auth: str = "password"
    ssh_pass: Optional[str] = None
    ssh_key: Optional[str] = None
    remote_path: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not re.match(r'^[a-zA-Z0-9._-]{2,40}$', v):
            raise ValueError("2-40 chars, letters, numbers, dots, underscores, hyphens")
        return v


@app.post("/api/projects", status_code=201)
async def create_project(body: CreateReq):
    if (PROJECTS_DIR / body.name).exists():
        raise HTTPException(409, f"'{body.name}' already exists")

    if body.backend == "ssh":
        if not body.ssh_host or not body.ssh_user:
            raise HTTPException(400, "SSH requires host and username")
        # Test SSH connection using system ssh command (supports more device types)
        ssh_opts = "-o StrictHostKeyChecking=no -o ConnectTimeout=5 -o KexAlgorithms=+diffie-hellman-group14-sha1 -o HostKeyAlgorithms=+ssh-rsa"
        if body.ssh_auth == "password":
            test_cmd = f"sshpass -p '{body.ssh_pass}' ssh {ssh_opts} -p {body.ssh_port} {body.ssh_user}@{body.ssh_host} exit"
        else:
            # Key auth — write temp key file
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False)
            tmp.write(body.ssh_key or "")
            tmp.close()
            os.chmod(tmp.name, 0o600)
            test_cmd = f"ssh {ssh_opts} -i {tmp.name} -p {body.ssh_port} {body.ssh_user}@{body.ssh_host} exit"
        r = run(["bash", "-c", test_cmd])
        # Some devices (switches/routers) reject 'exit' command but still connect
        # Accept if return code is 0 or if stderr doesn't contain connection/auth errors
        conn_errors = ["permission denied", "connection refused", "connection timed out", "no route to host", "could not resolve"]
        stderr_lower = (r.stderr or "").lower()
        if any(e in stderr_lower for e in conn_errors):
            raise HTTPException(400, f"SSH connection failed: {r.stderr.strip()}")

        # Save to registry
        reg = load_registry()
        reg[body.name] = {
            "backend": "ssh",
            "ssh_host": body.ssh_host,
            "ssh_port": body.ssh_port,
            "ssh_user": body.ssh_user,
            "ssh_auth": body.ssh_auth,
            "ssh_pass": body.ssh_pass if body.ssh_auth == "password" else None,
            "ssh_key": body.ssh_key if body.ssh_auth == "key" else None,
            "remote_path": body.remote_path or f"/home/{body.ssh_user}",
        }
        save_registry(reg)

        # Create local project dir + CLAUDE.md
        r = run([str(BIN_DIR / "project-create.sh"), body.name, "ssh",
                 body.ssh_host, str(body.ssh_port), body.ssh_user,
                 reg[body.name]["remote_path"], body.ssh_auth])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr or r.stdout)
    else:
        # Local backend
        reg = load_registry()
        reg[body.name] = {"backend": "local"}
        save_registry(reg)
        r = run([str(BIN_DIR / "project-create.sh"), body.name])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr or r.stdout)

    return get_status(body.name)


class UpdateReq(BaseModel):
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_auth: Optional[str] = None
    ssh_pass: Optional[str] = None
    ssh_key: Optional[str] = None
    remote_path: Optional[str] = None
    skip_permissions: Optional[bool] = None


@app.put("/api/projects/{name}")
async def update_project(name: str, body: UpdateReq):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    reg = load_registry()
    meta = reg.get(name, {})
    for field in ["ssh_host", "ssh_port", "ssh_user", "ssh_auth", "ssh_pass", "ssh_key", "remote_path", "skip_permissions"]:
        val = getattr(body, field)
        if val is not None:
            meta[field] = val
    reg[name] = meta
    save_registry(reg)
    return get_status(name)


@app.get("/api/projects/{name}/settings")
async def get_project_settings(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    meta = get_project_meta(name)
    # Never expose password directly, just indicate if set
    safe = {k: v for k, v in meta.items() if k != "ssh_pass"}
    safe["has_password"] = bool(meta.get("ssh_pass"))
    return safe


@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    r = run([str(BIN_DIR / "project-delete.sh"), name])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    # Remove from registry
    reg = load_registry()
    reg.pop(name, None)
    save_registry(reg)
    return {"deleted": name}


@app.post("/api/projects/{name}/start")
async def start_project(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    r = run([str(BIN_DIR / "project-start.sh"), name])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return get_status(name)


@app.post("/api/projects/{name}/stop")
async def stop_project(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    run([str(BIN_DIR / "project-stop.sh"), name])
    return get_status(name)


@app.get("/api/projects/{name}/status")
async def project_status(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    return get_status(name)


@app.post("/api/projects/{name}/send-keys")
async def send_keys(name: str, keys: str = "C-c", window: Optional[str] = None, session: Optional[str] = None):
    allowed = {"C-c", "C-z", "C-d", "C-\\", "Tab", "BTab", "Up", "Down", "Left", "Right",
               "Enter", "Escape", "Space",
               "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
               "y", "n", "Y", "N"}
    if keys not in allowed:
        raise HTTPException(400, f"keys must be one of: {allowed}")
    if not session:
        session = "system" if name == "_system" else f"proj-{name}"
    target = f"{session}:{window}" if window is not None else session
    # Single chars need -l flag (literal), special keys don't
    if len(keys) == 1:
        r = run(["tmux", "send-keys", "-t", target, "-l", keys])
    else:
        r = run(["tmux", "send-keys", "-t", target, keys])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return {"sent": keys, "session": session}


@app.get("/api/projects/{name}/tmux-windows")
async def tmux_windows(name: str):
    """List tmux windows for a project"""
    if name == "_system":
        session = "system"
    else:
        if not (PROJECTS_DIR / name).exists():
            raise HTTPException(404, f"'{name}' not found")
        session = f"proj-{name}"
    r = run(["tmux", "list-windows", "-t", session, "-F", "#{window_index} #{window_name} #{window_active}"])
    if r.returncode != 0:
        return {"windows": []}
    windows = []
    for line in r.stdout.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split(' ', 2)
        if len(parts) >= 3:
            windows.append({"index": int(parts[0]), "name": parts[1], "active": parts[2] == '1'})
    return {"windows": windows}


@app.post("/api/projects/{name}/tmux-select-window")
async def tmux_select_window(name: str, index: int):
    """Select a tmux window"""
    if name == "_system":
        session = "system"
    else:
        if not (PROJECTS_DIR / name).exists():
            raise HTTPException(404, f"'{name}' not found")
        session = f"proj-{name}"
    r = run(["tmux", "select-window", "-t", f"{session}:{index}"])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return {"selected": index}


class NewWindowReq(BaseModel):
    name: str = "shell"

@app.post("/api/projects/{name}/tmux-new-window")
async def tmux_new_window(name: str, body: NewWindowReq):
    """Create a new tmux window"""
    if name == "_system":
        session = "system"
    else:
        if not (PROJECTS_DIR / name).exists():
            raise HTTPException(404, f"'{name}' not found")
        session = f"proj-{name}"
    workdir = str(PROJECTS_DIR / name) if name != "_system" else str(Path.home())
    r = run(["tmux", "new-window", "-t", session, "-n", body.name, "-c", workdir])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return {"created": body.name}


@app.get("/api/projects/{name}/terminal-output")
async def terminal_output(name: str, lines: int = 200, window: Optional[str] = None):
    """Capture tmux pane output for chat-style display"""
    if name == "_system":
        session = "system"
    else:
        if not (PROJECTS_DIR / name).exists():
            raise HTTPException(404, f"'{name}' not found")
        session = f"proj-{name}"
    # Target specific window if provided
    target = f"{session}:{window}" if window is not None else session
    # Capture with scrollback and ANSI escape codes
    r = run(["tmux", "capture-pane", "-t", target, "-p", "-e", "-S", f"-{lines}"])
    if r.returncode != 0:
        return {"output": ""}
    return {"output": r.stdout}


class SendTextReq(BaseModel):
    text: str

@app.post("/api/projects/{name}/send-text")
async def send_text(name: str, body: SendTextReq, window: Optional[str] = None, session: Optional[str] = None):
    if not session:
        session = "system" if name == "_system" else f"proj-{name}"
    target = f"{session}:{window}" if window is not None else session
    if len(body.text) < 200:
        r = run(["tmux", "send-keys", "-t", target, "-l", body.text])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr)
    else:
        # Long text: use tmux buffer to avoid bracketed paste issues
        tmp = Path("/tmp/oopsbox-input.txt")
        tmp.write_text(body.text)
        run(["tmux", "load-buffer", str(tmp)])
        run(["tmux", "paste-buffer", "-t", target, "-d"])
    run(["tmux", "send-keys", "-t", target, "Enter"])
    return {"sent": body.text, "session": session}


# ── JSONL Session Messages API ────────────────────────────────────────────────

def _get_session_dir(name: str) -> Path:
    if name == "_system":
        path = str(Path.home())
    else:
        path = str(PROJECTS_DIR / name)
    # Claude Code replaces non-alphanumeric chars with - in dir hash
    import re as _re
    hash_name = _re.sub(r'[^a-zA-Z0-9]', '-', path)
    return Path.home() / ".claude" / "projects" / hash_name


def _extract_text(content) -> str:
    if isinstance(content, str):
        if content.strip().startswith("<command-"):
            return ""
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                t = item.get("text", "").strip()
                if t:
                    parts.append(t)
            elif item.get("type") == "tool_use":
                name = item.get("name", "")
                inp = item.get("input", {})
                desc = inp.get("description", "")
                fp = inp.get("file_path", "")
                cmd = inp.get("command", "")
                pattern = inp.get("pattern", "")
                header = f"**{name}**"
                if fp:
                    header += f" `{fp}`"
                elif pattern:
                    header += f" `{pattern}`"
                if desc:
                    header += f" — {desc}"
                if cmd:
                    parts.append(f"{header}\n```bash\n{cmd}\n```")
                else:
                    parts.append(header)
            elif item.get("type") == "tool_result":
                tc = item.get("content", "")
                if isinstance(tc, str) and tc.strip():
                    # Normalize line number prefixes to fixed width
                    # e.g. "  516→\tcode" → "516 │ code"
                    lines = tc.strip().split("\n")
                    cleaned = []
                    has_line_nums = any(re.match(r'\s*\d+→', l) for l in lines[:5])
                    if has_line_nums:
                        for line in lines:
                            m = re.match(r'\s*(\d+)→\t?(.*)', line)
                            if m:
                                cleaned.append(f"{m.group(1):>4} │ {m.group(2)}")
                            else:
                                cleaned.append(line)
                        output = "\n".join(cleaned)
                    else:
                        output = tc.strip()
                    if len(output) > 500:
                        output = output[:500] + "\n... (truncated)"
                    parts.append(f"```\n{output}\n```")
        return "\n\n".join(p for p in parts if p)
    return ""


# Cache for parsed JSONL messages: {file_path: (mtime, size, merged_messages)}
_session_cache: dict = {}


def _parse_jsonl(filepath: Path, max_lines: int = 2000) -> list:
    messages = []
    try:
        # For large files, only read last N lines
        size = filepath.stat().st_size
        if size > 500_000:  # > 500KB, read from tail
            import subprocess as _sp
            r = _sp.run(["tail", "-n", str(max_lines), str(filepath)], capture_output=True, text=True)
            lines = r.stdout.strip().split("\n") if r.returncode == 0 else []
        else:
            lines = filepath.open().readlines()
        for line in lines:
            d = json.loads(line)
            if d.get("type") not in ("user", "assistant"):
                continue
            if d.get("isMeta"):
                continue
            msg = d.get("message", {})
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            role = msg.get("role", "")
            if isinstance(content, list) and role == "user":
                has_user_text = any(
                    isinstance(item, dict) and item.get("type") == "text" and item.get("text", "").strip()
                    for item in content
                )
                has_tool_result = any(
                    isinstance(item, dict) and item.get("type") == "tool_result"
                    for item in content
                )
                if not has_user_text and has_tool_result:
                    role = "tool_output"
                elif not has_user_text:
                    continue
            text = _extract_text(content)
            if not text:
                continue
            messages.append({"role": role, "text": text, "ts": d.get("timestamp", "")})
    except Exception:
        pass
    # Merge assistant tool_use + following tool_output
    merged = []
    for m in messages:
        if m["role"] == "tool_output" and merged and merged[-1]["role"] == "assistant":
            merged[-1]["text"] += "\n\n" + m["text"]
        else:
            merged.append(m)
    return merged


@app.get("/api/projects/{name}/session-messages")
async def session_messages(name: str, after: int = 0):
    session_dir = _get_session_dir(name)
    if not session_dir.exists():
        return {"messages": [], "total": 0, "session_file": ""}

    files = sorted(session_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return {"messages": [], "total": 0, "session_file": ""}

    # Pick the most recently modified file that's actively being used
    # If the latest file is very small and there's a bigger recent one, prefer the bigger one
    fp = files[0]
    if len(files) > 1 and fp.stat().st_size < 2000:
        # Check if the second file was modified recently (within 1 hour)
        import time
        if time.time() - files[1].stat().st_mtime < 3600:
            fp = files[1]
    st = fp.stat()
    cache_key = str(fp)
    cached = _session_cache.get(cache_key)

    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        merged = cached[2]
    else:
        merged = _parse_jsonl(fp)
        _session_cache[cache_key] = (st.st_mtime, st.st_size, merged)

    return {
        "messages": merged[after:],
        "total": len(merged),
        "session_file": fp.name,
    }


@app.get("/api/projects/{name}/prompt-state")
async def prompt_state(name: str):
    # All AI agents live in "agents" session, window = project name or "system"
    window = "system" if name == "_system" else name
    r = run(["tmux", "capture-pane", "-t", f"agents:{window}", "-p", "-S", "-8"])
    lines = r.stdout.strip().split("\n") if r.returncode == 0 else []

    # Parse choices from bottom up — only take the latest group
    choices = []
    for line in reversed(lines):
        stripped = line.replace("\u00a0", " ").strip()
        if not stripped:
            continue
        # Skip hint lines
        if "Esc to cancel" in stripped or "Enter to confirm" in stripped or "Tab to amend" in stripped:
            continue
        # Numbered choice: "❯ 1. Yes" or "  2. No, exit"
        m = re.match(r'[❯\s]*(\d+)\.\s+(.+)', stripped)
        if m:
            choices.insert(0, {"num": m.group(1), "text": m.group(2).strip()})
            continue
        # Checkbox: "[x] Bash" or "[ ] Edit"
        m = re.match(r'[❯\s]*\[([ xX])\]\s+(.+)', stripped)
        if m:
            choices.insert(0, {"checked": m.group(1).lower() == "x", "text": m.group(2).strip()})
            continue
        # Not a choice line — stop (don't look at older lines)
        if choices:
            break

    # Check if tmux window exists and Claude is running
    if r.returncode != 0:
        return {"state": "no_session", "choices": [], "raw": []}

    # Detect if Claude crashed (bash prompt visible)
    all_text = "\n".join(lines)
    if re.search(r'\$\s*$', all_text) and "❯" not in all_text and "claude" not in all_text.lower():
        # Looks like bash prompt, not Claude
        return {"state": "claude_stopped", "choices": [], "raw": lines[-5:] if lines else []}

    # Detect prompt state from last non-empty line
    last = ""
    for line in reversed(lines):
        s = line.replace("\u00a0", " ").strip()
        if s:
            last = s
            break

    if choices:
        state = "waiting_choice"
    elif re.match(r'^[❯›>]\s*$', last):
        state = "waiting_text"
    elif any(c in last for c in "◐◑◒◓") or re.search(r'Thinking|Herding|Garnishing|Cogitating|Searching|Reading', last):
        state = "thinking"
    else:
        state = "idle"

    return {"state": state, "choices": choices, "raw": lines[-5:] if lines else []}


@app.get("/api/projects/{name}/clipboard")
async def get_clipboard(name: str):
    """Read tmux copy buffer (last copied text)"""
    session = "system" if name == "_system" else f"proj-{name}"
    r = run(["tmux", "show-buffer", "-t", session])
    if r.returncode != 0:
        r = run(["tmux", "show-buffer"])
    return {"text": r.stdout if r.returncode == 0 else ""}


@app.post("/api/projects/{name}/mouse")
async def toggle_mouse(name: str, on: bool = True):
    """Toggle tmux mouse mode for a session"""
    session = "system" if name == "_system" else f"proj-{name}"
    val = "on" if on else "off"
    r = run(["tmux", "set", "-t", session, "mouse", val])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return {"mouse": val}


THEME_CONF = Path("/home/mountain/.config/ttyd-theme.conf")

@app.get("/api/terminal-theme")
async def get_terminal_theme():
    theme = "dark"
    if THEME_CONF.exists():
        for line in THEME_CONF.read_text().splitlines():
            if line.strip().startswith("TTYD_THEME="):
                theme = line.split("=", 1)[1].strip()
    return {"theme": theme}


@app.post("/api/terminal-theme/{theme}")
async def set_terminal_theme(theme: str):
    if theme not in ("dark", "light"):
        raise HTTPException(400, "theme must be 'dark' or 'light'")
    if not THEME_CONF.exists():
        raise HTTPException(500, "theme config not found")
    content = THEME_CONF.read_text()
    import re as _re
    content = _re.sub(r'^TTYD_THEME=\w+', f'TTYD_THEME={theme}', content, flags=_re.MULTILINE)
    THEME_CONF.write_text(content)
    # Restart all ttyd instances to apply
    for name in project_names():
        run([str(BIN_DIR / "project-stop.sh"), name])
        run([str(BIN_DIR / "project-start.sh"), name])
    return {"theme": theme, "restarted": project_names()}


@app.get("/api/system")
async def system_stats():
    import shutil
    # CPU
    r_cpu = run(["awk", "{print $1,$2,$3}", "/proc/loadavg"])
    load = r_cpu.stdout.strip() if r_cpu.returncode == 0 else "?"
    r_ncpu = run(["nproc"])
    ncpu = int(r_ncpu.stdout.strip()) if r_ncpu.returncode == 0 else 1
    # CPU usage from /proc/stat snapshot
    r_stat = run(["head", "-1", "/proc/stat"])
    cpu_pct = 0
    if r_stat.returncode == 0:
        parts = r_stat.stdout.split()
        if len(parts) >= 8:
            user, nice, system, idle = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
            total = user + nice + system + idle
            cpu_pct = round((total - idle) / total * 100, 1) if total > 0 else 0
    # RAM
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            mem[k.strip()] = int(v.strip().split()[0])
    ram_total = mem.get("MemTotal", 0) // 1024
    ram_avail = mem.get("MemAvailable", 0) // 1024
    ram_used = ram_total - ram_avail
    ram_pct = round(ram_used / ram_total * 100, 1) if ram_total > 0 else 0
    # Swap
    swap_total = mem.get("SwapTotal", 0) // 1024
    swap_free = mem.get("SwapFree", 0) // 1024
    swap_used = swap_total - swap_free
    swap_pct = round(swap_used / swap_total * 100, 1) if swap_total > 0 else 0
    # Disk — parse df for accurate values matching what users see
    r_df = run(["df", "--output=size,used,pcent", "-BG", "/"])
    disk_total = disk_used = 0
    disk_pct = 0.0
    if r_df.returncode == 0:
        lines = r_df.stdout.strip().split('\n')
        if len(lines) >= 2:
            parts = lines[1].split()
            disk_total = int(parts[0].rstrip('G'))
            disk_used = int(parts[1].rstrip('G'))
            disk_pct = float(parts[2].rstrip('%'))
    return {
        "cpu": {"percent": cpu_pct, "load": load, "cores": ncpu},
        "ram": {"used_mb": ram_used, "total_mb": ram_total, "percent": ram_pct},
        "swap": {"used_mb": swap_used, "total_mb": swap_total, "percent": swap_pct},
        "disk": {"used_gb": disk_used, "total_gb": disk_total, "percent": disk_pct},
    }


# ── File browser API (local + SFTP) ──────────────────────────────────────────

def _get_base(project: str) -> Path:
    if project == "_system":
        return Path("/home/mountain")
    return PROJECTS_DIR / project

def _is_ssh_project(project: str) -> bool:
    if project == "_system":
        return False
    meta = get_project_meta(project)
    return meta.get("backend") == "ssh"

def _get_remote_base(project: str) -> str:
    meta = get_project_meta(project)
    return meta.get("remote_path", "/home/" + meta.get("ssh_user", ""))


@app.get("/api/files/{project}")
async def list_files(project: str, path: str = ""):
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            try:
                stat = sftp.stat(target)
            except FileNotFoundError:
                raise HTTPException(404, "path not found")
            import stat as stat_mod
            if not stat_mod.S_ISDIR(stat.st_mode):
                return {"type": "file", "path": path, "name": os.path.basename(path)}
            entries = []
            for attr in sorted(sftp.listdir_attr(target), key=lambda a: (not stat_mod.S_ISDIR(a.st_mode), a.filename)):
                if attr.filename.startswith(".") and attr.filename in (".git", ".code-server"):
                    continue
                is_dir = stat_mod.S_ISDIR(attr.st_mode)
                rel = os.path.join(path, attr.filename) if path else attr.filename
                entries.append({
                    "name": attr.filename,
                    "type": "dir" if is_dir else "file",
                    "path": rel,
                    "size": attr.st_size if not is_dir else None,
                    "mtime": attr.st_mtime,
                })
            sftp.close(); ssh.close()
            return {"type": "dir", "path": path, "entries": entries}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    if not target.exists():
        raise HTTPException(404, "path not found")
    if target.is_file():
        return {"type": "file", "path": path, "name": target.name}
    entries = []
    for item in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name)):
        if item.name.startswith(".") and item.name in (".git", ".code-server"):
            continue
        st = item.stat()
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "path": str(item.relative_to(base)),
            "size": st.st_size if item.is_file() else None,
            "mtime": st.st_mtime,
        })
    return {"type": "dir", "path": path, "entries": entries}


@app.get("/api/files/{project}/read")
async def read_file(project: str, path: str):
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            stat = sftp.stat(target)
            if stat.st_size > 2 * 1024 * 1024:
                raise HTTPException(413, "file too large (>2MB)")
            with sftp.open(target, "r") as f:
                content = f.read().decode("utf-8", errors="replace")
            sftp.close(); ssh.close()
            return {"path": path, "name": os.path.basename(path), "content": content}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP read error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    if target.stat().st_size > 2 * 1024 * 1024:
        raise HTTPException(413, "file too large (>2MB)")
    try:
        content = target.read_text(errors="replace")
    except Exception:
        raise HTTPException(400, "cannot read file")
    return {"path": path, "name": target.name, "content": content}


class SaveReq(BaseModel):
    content: str

class MoveReq(BaseModel):
    dest: str

@app.put("/api/files/{project}/write")
async def write_file(project: str, path: str, body: SaveReq):
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            # Ensure parent dir exists
            parent = os.path.dirname(target)
            try:
                sftp.stat(parent)
            except FileNotFoundError:
                ssh.exec_command(f"mkdir -p {parent}")
            with sftp.open(target, "w") as f:
                f.write(body.content.encode("utf-8"))
            sftp.close(); ssh.close()
            return {"saved": path}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP write error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content)
    return {"saved": path}

@app.post("/api/files/{project}/move")
async def move_file(project: str, path: str, body: MoveReq):
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            src = os.path.normpath(os.path.join(remote_base, path))
            dst = os.path.normpath(os.path.join(remote_base, body.dest))
            if not src.startswith(remote_base) or not dst.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            sftp.rename(src, dst)
            sftp.close(); ssh.close()
            return {"moved": path, "to": body.dest}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP move error: {str(e)}")
    # Local
    import shutil as _shutil
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    src = (base / path).resolve()
    dst = (base / body.dest).resolve()
    if not str(src).startswith(str(base.resolve())) or not str(dst).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    if not src.exists():
        raise HTTPException(404, "source not found")
    if dst.exists():
        raise HTTPException(409, "destination already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _shutil.move(str(src), str(dst))
    return {"moved": path, "to": body.dest}


# ── File upload/download ─────────────────────────────────────────────────────

@app.post("/api/files/{project}/upload")
async def upload_file(project: str, path: str, file: UploadFile = File(...)):
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(413, "file too large (>50MB)")
    filename = file.filename or "upload"
    target_path = os.path.join(path, filename) if path else filename

    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, target_path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            parent = os.path.dirname(target)
            try:
                sftp.stat(parent)
            except FileNotFoundError:
                ssh.exec_command(f"mkdir -p {parent}")
            with sftp.open(target, "wb") as f:
                f.write(content)
            sftp.close(); ssh.close()
            return {"uploaded": target_path}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP upload error: {str(e)}")
    # Local
    base = _get_base(project)
    target = (base / target_path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return {"uploaded": target_path}


from starlette.responses import StreamingResponse

@app.get("/api/files/{project}/download")
async def download_file(project: str, path: str):
    filename = os.path.basename(path)
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            buf = io.BytesIO()
            sftp.getfo(target, buf)
            sftp.close(); ssh.close()
            buf.seek(0)
            return StreamingResponse(buf, media_type="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP download error: {str(e)}")
    # Local
    base = _get_base(project)
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(str(target), filename=filename)


# ── Channel API ───────────────────────────────────────────────────────────────

CHANNEL_REGISTRY = Path("/home/mountain/projects/.channel-registry.json")
CHANNELS_DIR = Path("/home/mountain/channels")

def load_channels() -> dict:
    if CHANNEL_REGISTRY.exists():
        return json.loads(CHANNEL_REGISTRY.read_text())
    return {}

def save_channels(reg: dict):
    CHANNEL_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    CHANNEL_REGISTRY.write_text(json.dumps(reg, indent=2))
    os.chmod(str(CHANNEL_REGISTRY), 0o600)

def get_channel_status(name: str) -> dict:
    meta = load_channels().get(name, {})
    window = f"chan-{name}"
    # Check if window exists in agents session
    r = run(["tmux", "list-windows", "-t", "agents", "-F", "#{window_name}"])
    windows = r.stdout.strip().split("\n") if r.returncode == 0 else []
    status = "running" if window in windows else "idle"
    return {"name": name, "status": status, "workdir": meta.get("workdir", "/home/mountain"),
            "skip_permissions": meta.get("skip_permissions", False)}


@app.get("/api/channels")
async def list_channels():
    reg = load_channels()
    return {"channels": [get_channel_status(n) for n in reg]}


class ChannelCreateReq(BaseModel):
    name: str
    workdir: str = "/home/mountain"
    skip_permissions: bool = False
    telegram_token: str = ""

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not re.match(r'^[a-zA-Z0-9._-]{2,40}$', v):
            raise ValueError("2-40 chars: letters, numbers, dots, underscores, hyphens")
        return v


@app.post("/api/channels", status_code=201)
async def create_channel(body: ChannelCreateReq):
    reg = load_channels()
    if body.name in reg:
        raise HTTPException(409, f"Channel '{body.name}' already exists")
    # Create channel directory
    chan_dir = CHANNELS_DIR / body.name
    chan_dir.mkdir(parents=True, exist_ok=True)
    workdir = body.workdir if body.workdir and body.workdir != "/home/mountain" else str(chan_dir)
    reg[body.name] = {
        "workdir": workdir,
        "skip_permissions": body.skip_permissions,
        "telegram_token": body.telegram_token,
    }
    save_channels(reg)
    return get_channel_status(body.name)


class ChannelUpdateReq(BaseModel):
    workdir: Optional[str] = None
    skip_permissions: Optional[bool] = None


@app.put("/api/channels/{name}")
async def update_channel(name: str, body: ChannelUpdateReq):
    reg = load_channels()
    if name not in reg:
        raise HTTPException(404, f"Channel '{name}' not found")
    if body.workdir is not None:
        reg[name]["workdir"] = body.workdir
    if body.skip_permissions is not None:
        reg[name]["skip_permissions"] = body.skip_permissions
    save_channels(reg)
    return get_channel_status(name)


@app.delete("/api/channels/{name}")
async def delete_channel(name: str):
    reg = load_channels()
    if name not in reg:
        raise HTTPException(404, f"Channel '{name}' not found")
    # Stop if running
    run([str(BIN_DIR / "channel-stop.sh"), name])
    del reg[name]
    save_channels(reg)
    return {"deleted": name}


@app.post("/api/channels/{name}/start")
async def start_channel(name: str):
    reg = load_channels()
    if name not in reg:
        raise HTTPException(404, f"Channel '{name}' not found")
    r = run([str(BIN_DIR / "channel-start.sh"), name])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return get_channel_status(name)


@app.post("/api/channels/{name}/stop")
async def stop_channel(name: str):
    reg = load_channels()
    if name not in reg:
        raise HTTPException(404, f"Channel '{name}' not found")
    run([str(BIN_DIR / "channel-stop.sh"), name])
    return get_channel_status(name)


@app.get("/api/channels/{name}/log")
async def channel_log(name: str, lines: int = 50):
    window = f"chan-{name}"
    r = run(["tmux", "capture-pane", "-t", f"agents:{window}", "-p", "-S", f"-{lines}"])
    return {"output": r.stdout if r.returncode == 0 else ""}


# ── Static frontend ───────────────────────────────────────────────────────────

from fastapi.responses import Response

app.mount("/static", StaticFiles(directory="/opt/dashboard/static"), name="static")

@app.get("/")
async def root():
    return FileResponse("/opt/dashboard/static/index.html")

