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
import shlex
import paramiko
import io

app = FastAPI(title="OopsBox")

# ── Auth ─────────────────────────────────────────────────────────────────────

AUTH_FILE = Path.home() / ".config" / "oopsbox" / "auth.json"
SESSION_FILE = Path.home() / ".config" / "oopsbox" / "sessions.json"
SESSION_TTL = 86400  # 24h

def _load_sessions() -> dict:
    if SESSION_FILE.exists():
        try:
            data = json.loads(SESSION_FILE.read_text())
            # Purge expired
            now = time.time()
            return {k: v for k, v in data.items() if v > now}
        except Exception:
            pass
    return {}

def _save_sessions():
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(SESSIONS))
    os.chmod(str(SESSION_FILE), 0o600)

SESSIONS = _load_sessions()

PBKDF2_ITERATIONS = 600_000

def hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
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
    salt = auth.get("salt", "")
    stored_hash = auth.get("password_hash", "")
    # Try PBKDF2 first
    hashed, _ = hash_password(password, salt)
    if hashed == stored_hash:
        return True
    # Fall back to legacy SHA-256 for migration
    legacy = hashlib.sha256((salt + password).encode()).hexdigest()
    if legacy == stored_hash:
        # Auto-upgrade to PBKDF2
        new_hash, _ = hash_password(password, salt)
        auth["password_hash"] = new_hash
        AUTH_FILE.write_text(json.dumps(auth, indent=2))
        return True
    return False

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
        _save_sessions()
        return False
    return True

class LoginReq(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def api_login(body: LoginReq, request: Request):
    if verify_password(body.username, body.password):
        token = secrets.token_hex(32)
        SESSIONS[token] = time.time() + SESSION_TTL
        _save_sessions()
        resp = JSONResponse({"ok": True})
        # Set secure flag when accessed via HTTPS
        is_https = request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
        resp.set_cookie("oopsbox_session", token, max_age=SESSION_TTL, httponly=True,
                        samesite="lax", path="/", secure=is_https)
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
    _save_sessions()
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

_HOME = Path(os.environ.get("OOPSBOX_HOME", str(Path.home())))
PROJECTS_DIR = _HOME / "projects"
BIN_DIR      = _HOME / "bin"
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
    if status["backend"] == "container":
        status["container_name"] = meta.get("container_name", "")
        status["container_type"] = meta.get("container_type", "docker")
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
    # Decrypt SSH credentials (support both encrypted and legacy plaintext)
    if meta.get("ssh_auth") == "key":
        key_text = _decrypt_token(meta.get("ssh_key_enc", "")) or meta.get("ssh_key", "")
        if key_text:
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(key_text))
            auth["pkey"] = pkey
    else:
        ssh_pass = _decrypt_token(meta.get("ssh_pass_enc", "")) or meta.get("ssh_pass", "")
        auth["password"] = ssh_pass
    # Support legacy SSH algorithms for older devices
    auth["disabled_algorithms"] = {}
    auth["allow_agent"] = False
    auth["look_for_keys"] = False
    try:
        ssh.connect(**auth, timeout=10)
    except paramiko.SSHException:
        # Retry with legacy transport settings
        ssh_pass = _decrypt_token(meta.get("ssh_pass_enc", "")) or meta.get("ssh_pass", "")
        transport = paramiko.Transport((meta["ssh_host"], meta.get("ssh_port", 22)))
        transport.connect(username=meta["ssh_user"], password=ssh_pass)
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
    isolated: bool = False
    mem_limit: str = "4g"
    cpu_limit: str = "2.0"
    api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    container_name: Optional[str] = None
    container_type: str = "docker"  # "docker" or "lxc"
    container_user: str = "root"
    container_path: str = "/root"
    skip_permissions: bool = False

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
        ssh_opts = ["-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5",
                    "-o", "KexAlgorithms=+diffie-hellman-group14-sha1",
                    "-o", "HostKeyAlgorithms=+ssh-rsa"]
        tmp_key_file = None
        try:
            if body.ssh_auth == "password":
                cmd = ["sshpass", "-p", body.ssh_pass or ""] + ["ssh"] + ssh_opts + \
                      ["-p", str(body.ssh_port), f"{body.ssh_user}@{body.ssh_host}", "exit"]
            else:
                # Key auth — write temp key file
                import tempfile
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.key', delete=False)
                tmp.write(body.ssh_key or "")
                tmp.close()
                os.chmod(tmp.name, 0o600)
                tmp_key_file = tmp.name
                cmd = ["ssh"] + ssh_opts + ["-i", tmp.name, "-p", str(body.ssh_port),
                       f"{body.ssh_user}@{body.ssh_host}", "exit"]
            r = run(cmd)
        finally:
            if tmp_key_file:
                try:
                    os.unlink(tmp_key_file)
                except OSError:
                    pass
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
            "ssh_pass_enc": _encrypt_token(body.ssh_pass) if body.ssh_auth == "password" and body.ssh_pass else None,
            "ssh_key_enc": _encrypt_token(body.ssh_key) if body.ssh_auth == "key" and body.ssh_key else None,
            "remote_path": body.remote_path or f"/home/{body.ssh_user}",
        }
        save_registry(reg)

        # Create local project dir + CLAUDE.md
        r = run([str(BIN_DIR / "project-create.sh"), body.name, "ssh",
                 body.ssh_host, str(body.ssh_port), body.ssh_user,
                 reg[body.name]["remote_path"], body.ssh_auth])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr or r.stdout)
    elif body.backend == "container":
        if not body.container_name:
            raise HTTPException(400, "Container name is required")
        ct = body.container_type  # docker or lxc
        # Verify container exists
        if ct == "lxc":
            r = run(["lxc", "info", body.container_name])
        else:
            r = run(["docker", "inspect", body.container_name])
        if r.returncode != 0:
            raise HTTPException(400, f"Container '{body.container_name}' not found ({ct})")
        reg = load_registry()
        reg[body.name] = {
            "backend": "container",
            "container_name": body.container_name,
            "container_type": ct,
            "container_user": body.container_user or "root",
            "container_path": body.container_path or "/root",
        }
        save_registry(reg)
        r = run([str(BIN_DIR / "project-create.sh"), body.name, "container",
                 body.container_name, ct, body.container_user or "root",
                 body.container_path or "/root"])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr or r.stdout)
    else:
        # Local backend
        reg = load_registry()
        entry = {"backend": "local"}
        if body.isolated:
            entry["isolated"] = True
            entry["mem_limit"] = body.mem_limit
            entry["cpu_limit"] = body.cpu_limit
        reg[body.name] = entry
        save_registry(reg)
        r = run([str(BIN_DIR / "project-create.sh"), body.name])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr or r.stdout)

    # Store encrypted API key if provided
    if body.api_key:
        reg = load_registry()
        reg[body.name]["api_key_enc"] = _encrypt_token(body.api_key)
        save_registry(reg)

    # Store ANTHROPIC_BASE_URL if provided (for LiteLLM proxy)
    if body.anthropic_base_url:
        reg = load_registry()
        reg[body.name]["anthropic_base_url"] = body.anthropic_base_url
        save_registry(reg)

    # Store skip_permissions if set
    if body.skip_permissions:
        reg = load_registry()
        reg[body.name]["skip_permissions"] = True
        save_registry(reg)

    # Auto-start the project (launches Claude session + ttyd)
    run([str(BIN_DIR / "project-start.sh"), body.name])

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
    isolated: Optional[bool] = None
    mem_limit: Optional[str] = None
    cpu_limit: Optional[str] = None
    api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    container_name: Optional[str] = None
    container_type: Optional[str] = None
    container_user: Optional[str] = None
    container_path: Optional[str] = None


@app.put("/api/projects/{name}")
async def update_project(name: str, body: UpdateReq):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    reg = load_registry()
    meta = reg.get(name, {})
    for field in ["ssh_host", "ssh_port", "ssh_user", "ssh_auth", "remote_path", "skip_permissions", "isolated", "mem_limit", "cpu_limit", "container_name", "container_type", "container_user", "container_path"]:
        val = getattr(body, field)
        if val is not None:
            meta[field] = val
    # Encrypt SSH credentials
    if body.ssh_pass is not None:
        if body.ssh_pass:
            meta["ssh_pass_enc"] = _encrypt_token(body.ssh_pass)
            meta.pop("ssh_pass", None)  # remove legacy plaintext
        else:
            meta.pop("ssh_pass_enc", None)
            meta.pop("ssh_pass", None)
    if body.ssh_key is not None:
        if body.ssh_key:
            meta["ssh_key_enc"] = _encrypt_token(body.ssh_key)
            meta.pop("ssh_key", None)  # remove legacy plaintext
        else:
            meta.pop("ssh_key_enc", None)
            meta.pop("ssh_key", None)
    # Encrypt API key if provided; empty string clears it
    if body.api_key is not None:
        if body.api_key:
            meta["api_key_enc"] = _encrypt_token(body.api_key)
        else:
            meta.pop("api_key_enc", None)
    # ANTHROPIC_BASE_URL for LiteLLM proxy; empty string clears it
    if body.anthropic_base_url is not None:
        if body.anthropic_base_url:
            meta["anthropic_base_url"] = body.anthropic_base_url
        else:
            meta.pop("anthropic_base_url", None)
    reg[name] = meta
    save_registry(reg)
    return get_status(name)


@app.get("/api/projects/{name}/settings")
async def get_project_settings(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    meta = get_project_meta(name)
    # Never expose secrets, just indicate if set
    safe = {k: v for k, v in meta.items() if k not in ("ssh_pass", "ssh_pass_enc", "ssh_key", "ssh_key_enc", "api_key_enc")}
    safe["has_password"] = bool(meta.get("ssh_pass_enc") or meta.get("ssh_pass"))
    safe["has_api_key"] = bool(meta.get("api_key_enc"))
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
    allowed = {"C-c", "C-z", "C-d", "C-l", "C-\\", "Tab", "BTab", "Up", "Down", "Left", "Right",
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


# ── Chat file upload (to /tmp for agent reference) ────────────────────────────

@app.post("/api/chat-upload")
async def chat_upload(file: UploadFile = File(...)):
    import time
    filename = file.filename or "upload"
    # Sanitize filename
    safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)
    dest = Path("/tmp") / f"oopsbox-{int(time.time())}-{safe_name}"
    content = await file.read()
    dest.write_bytes(content)
    return {"path": str(dest), "filename": filename, "size": len(content)}


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


def _extract_tool_result_text(content) -> str:
    """Extract plain text from tool_result content (string or list of text items)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text", "").strip()
                if t:
                    parts.append(t)
        return "\n".join(parts)
    return ""


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
            ts = d.get("timestamp", "")

            if isinstance(content, list) and role == "assistant":
                # Extract text portions and tool_use items separately
                text_parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        t = item.get("text", "").strip()
                        if t:
                            text_parts.append(t)
                    elif item.get("type") == "tool_use":
                        messages.append({
                            "role": "tool_call",
                            "tool": item.get("name", "unknown"),
                            "input": item.get("input", {}),
                            "tool_use_id": item.get("id", ""),
                            "output_preview": "",
                            "output_full": "",
                            "status": "success",
                            "ts": ts,
                        })
                if text_parts:
                    messages.append({"role": "assistant", "text": "\n\n".join(text_parts), "ts": ts})
            elif isinstance(content, list) and role == "user":
                has_user_text = any(
                    isinstance(item, dict) and item.get("type") == "text" and item.get("text", "").strip()
                    for item in content
                )
                has_tool_result = any(
                    isinstance(item, dict) and item.get("type") == "tool_result"
                    for item in content
                )
                if not has_user_text and has_tool_result:
                    # Match each tool_result to its corresponding tool_call entry
                    for item in content:
                        if not isinstance(item, dict) or item.get("type") != "tool_result":
                            continue
                        tool_use_id = item.get("tool_use_id", "")
                        result_text = _extract_tool_result_text(item.get("content", ""))
                        is_error = bool(item.get("is_error"))
                        # Search backwards for matching tool_call
                        for prev in reversed(messages):
                            if prev.get("role") == "tool_call" and prev.get("tool_use_id") == tool_use_id:
                                prev["output_full"] = result_text
                                prev["output_preview"] = result_text[:200]
                                if is_error:
                                    prev["status"] = "error"
                                break
                elif not has_user_text:
                    continue
                else:
                    text = _extract_text(content)
                    if text:
                        messages.append({"role": "user", "text": text, "ts": ts})
            else:
                text = _extract_text(content)
                if not text:
                    continue
                messages.append({"role": role, "text": text, "ts": ts})
    except Exception:
        pass
    return messages


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

    # Use mtime_ns for sub-second cache invalidation
    mtime_ns = st.st_mtime_ns
    fsize = st.st_size
    if cached and cached[0] == mtime_ns and cached[1] == fsize:
        merged = cached[2]
    else:
        merged = _parse_jsonl(fp)
        _session_cache[cache_key] = (mtime_ns, fsize, merged)

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
    # Real interactive choices have ❯ marker on the selected item;
    # plain numbered lists in Claude's text responses do not.
    choices = []
    has_cursor = False
    for line in reversed(lines):
        stripped = line.replace("\u00a0", " ").strip()
        if not stripped:
            continue
        # Skip hint lines
        if "Esc to cancel" in stripped or "Enter to confirm" in stripped or "Tab to amend" in stripped:
            continue
        # Numbered choice: "❯ 1. Yes" or "  2. No, exit"
        m = re.match(r'([❯\s]*?)(\d+)\.\s+(.+)', stripped)
        if m:
            if "❯" in m.group(1):
                has_cursor = True
            choices.insert(0, {"num": m.group(2), "text": m.group(3).strip()})
            continue
        # Checkbox: "[x] Bash" or "[ ] Edit"
        m = re.match(r'([❯\s]*?)\[([ xX])\]\s+(.+)', stripped)
        if m:
            if "❯" in m.group(1):
                has_cursor = True
            choices.insert(0, {"checked": m.group(2).lower() == "x", "text": m.group(3).strip()})
            continue
        # Not a choice line — stop (don't look at older lines)
        if choices:
            break
    # Discard choices if no ❯ cursor found — they're just a numbered list in text
    if not has_cursor:
        choices = []

    # Check if tmux window exists and Claude is running
    if r.returncode != 0:
        return {"state": "no_session", "choices": [], "raw": []}

    # Detect if Claude crashed (bash prompt visible)
    all_text = "\n".join(lines)
    if re.search(r'\$\s*$', all_text) and "❯" not in all_text and "claude" not in all_text.lower():
        # Looks like bash prompt, not Claude
        return {"state": "claude_stopped", "choices": [], "raw": lines[-5:] if lines else []}

    # Detect prompt state from last few non-empty lines
    # Claude Code wraps ❯ prompt between separator lines, so check multiple lines
    tail_lines = []
    for line in reversed(lines):
        s = line.replace("\u00a0", " ").strip()
        if s:
            tail_lines.append(s)
        if len(tail_lines) >= 5:
            break
    tail_text = " ".join(tail_lines)

    if choices:
        state = "waiting_choice"
    elif any(re.match(r'^[❯›>]\s*$', t) for t in tail_lines):
        state = "waiting_text"
    elif any(c in tail_text for c in "◐◑◒◓") or re.search(r'Thinking|Herding|Garnishing|Cogitating|Searching|Reading', tail_text):
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


THEME_CONF = _HOME / ".config" / "ttyd-theme.conf"

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


_prev_cpu = None  # (total, idle) from previous call

@app.get("/api/system")
async def system_stats():
    global _prev_cpu
    import shutil
    # CPU
    r_cpu = run(["awk", "{print $1,$2,$3}", "/proc/loadavg"])
    load = r_cpu.stdout.strip() if r_cpu.returncode == 0 else "?"
    r_ncpu = run(["nproc"])
    ncpu = int(r_ncpu.stdout.strip()) if r_ncpu.returncode == 0 else 1
    # CPU usage: delta between two /proc/stat snapshots
    cpu_pct = 0.0
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()
        vals = [int(x) for x in parts[1:8]]
        total = sum(vals)
        idle = vals[3] + vals[4]  # idle + iowait
        if _prev_cpu:
            dt = total - _prev_cpu[0]
            di = idle - _prev_cpu[1]
            cpu_pct = round((dt - di) / dt * 100, 1) if dt > 0 else 0.0
        _prev_cpu = (total, idle)
    except Exception:
        pass
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
        return _HOME
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


MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB hard limit
CHUNK_LINES = 1000  # lines per chunk for sliding read


def _read_lines_chunk(text: str, offset: int, limit: int):
    """Extract a chunk of lines from text content."""
    lines = text.split("\n")
    total = len(lines)
    offset = max(0, min(offset, total))
    end = min(offset + limit, total)
    chunk = "\n".join(lines[offset:end])
    return chunk, total, offset, end


@app.get("/api/files/{project}/read")
async def read_file(project: str, path: str, offset: int = 0, limit: int = 0):
    """Read a file. If limit > 0, returns a sliding window of lines."""
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            stat = sftp.stat(target)
            if stat.st_size > MAX_FILE_SIZE:
                raise HTTPException(413, f"file too large (>{MAX_FILE_SIZE // 1024 // 1024}MB)")
            with sftp.open(target, "r") as f:
                content = f.read().decode("utf-8", errors="replace")
            sftp.close(); ssh.close()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP read error: {str(e)}")
    else:
        # Local
        base = _get_base(project)
        if not base.exists():
            raise HTTPException(404, f"'{project}' not found")
        target = (base / path).resolve()
        if not str(target).startswith(str(base.resolve())):
            raise HTTPException(403, "path traversal")
        if not target.is_file():
            raise HTTPException(404, "file not found")
        if target.stat().st_size > MAX_FILE_SIZE:
            raise HTTPException(413, f"file too large (>{MAX_FILE_SIZE // 1024 // 1024}MB)")
        try:
            content = target.read_text(errors="replace")
        except Exception:
            raise HTTPException(400, "cannot read file")

    total_lines = content.count("\n") + 1
    name = os.path.basename(path) if _is_ssh_project(project) else Path(path).name

    # Sliding window mode
    if limit > 0:
        chunk, total, start, end = _read_lines_chunk(content, offset, limit)
        return {"path": path, "name": name, "content": chunk,
                "total_lines": total, "offset": start, "end": end, "chunked": True}

    # Full file — but if too many lines, auto-chunk
    if total_lines > CHUNK_LINES:
        chunk, total, start, end = _read_lines_chunk(content, offset, CHUNK_LINES)
        return {"path": path, "name": name, "content": chunk,
                "total_lines": total, "offset": start, "end": end, "chunked": True}

    return {"path": path, "name": name, "content": content,
            "total_lines": total_lines, "chunked": False}


class SaveReq(BaseModel):
    content: str

class MoveReq(BaseModel):
    dest: str

class RenameReq(BaseModel):
    new_name: str

class DeleteReq(BaseModel):
    paths: list[str]

class MkdirReq(BaseModel):
    path: str

class CopyReq(BaseModel):
    paths: list[str]
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
                ssh.exec_command(f"mkdir -p {shlex.quote(parent)}")
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


@app.post("/api/files/{project}/rename")
async def rename_file(project: str, path: str, body: RenameReq):
    if "/" in body.new_name or "\\" in body.new_name:
        raise HTTPException(400, "new_name must not contain path separators")
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            src = os.path.normpath(os.path.join(remote_base, path))
            dst = os.path.normpath(os.path.join(os.path.dirname(src), body.new_name))
            if not src.startswith(remote_base) or not dst.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            try:
                sftp.stat(dst)
                raise HTTPException(409, "destination already exists")
            except HTTPException:
                raise
            except Exception:
                pass
            sftp.rename(src, dst)
            sftp.close(); ssh.close()
            new_rel = os.path.relpath(dst, remote_base)
            return {"renamed": path, "to": new_rel}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP rename error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    src = (base / path).resolve()
    dst = src.parent / body.new_name
    base_resolved = base.resolve()
    if not str(src).startswith(str(base_resolved)) or not str(dst).startswith(str(base_resolved)):
        raise HTTPException(403, "path traversal")
    if not src.exists():
        raise HTTPException(404, "source not found")
    if dst.exists():
        raise HTTPException(409, "destination already exists")
    src.rename(dst)
    new_rel = str(dst.relative_to(base_resolved))
    return {"renamed": path, "to": new_rel}


@app.post("/api/files/{project}/delete")
async def delete_files(project: str, body: DeleteReq):
    import shutil as _shutil
    deleted = []
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            for rel in body.paths:
                target = os.path.normpath(os.path.join(remote_base, rel))
                if not target.startswith(remote_base) or target == remote_base:
                    continue
                try:
                    stat = sftp.stat(target)
                    import stat as stat_mod
                    if stat_mod.S_ISDIR(stat.st_mode):
                        _, stdout, stderr = ssh.exec_command(f"rm -rf {shlex.quote(target)}")
                        stdout.channel.recv_exit_status()
                    else:
                        sftp.remove(target)
                    deleted.append(rel)
                except Exception:
                    pass
            sftp.close(); ssh.close()
            return {"deleted": deleted}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP delete error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    base_resolved = base.resolve()
    for rel in body.paths:
        target = (base / rel).resolve()
        if not str(target).startswith(str(base_resolved)) or target == base_resolved:
            continue
        if not target.exists():
            continue
        if target.is_dir():
            _shutil.rmtree(str(target))
        else:
            target.unlink()
        deleted.append(rel)
    return {"deleted": deleted}


@app.post("/api/files/{project}/mkdir")
async def make_directory(project: str, body: MkdirReq):
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            target = os.path.normpath(os.path.join(remote_base, body.path))
            if not target.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            try:
                sftp.stat(target)
                raise HTTPException(409, "directory already exists")
            except HTTPException:
                raise
            except Exception:
                pass
            _, stdout, _ = ssh.exec_command(f"mkdir -p {shlex.quote(target)}")
            stdout.channel.recv_exit_status()
            sftp.close(); ssh.close()
            return {"created": body.path}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP mkdir error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    target = (base / body.path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    if target.exists():
        raise HTTPException(409, "directory already exists")
    target.mkdir(parents=True, exist_ok=False)
    return {"created": body.path}


# ── Copy / Search / Zip-download ─────────────────────────────────────────────

import zipfile
import fnmatch

@app.post("/api/files/{project}/copy")
async def copy_files(project: str, body: CopyReq):
    import shutil as _shutil
    copied = []
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            dest = os.path.normpath(os.path.join(remote_base, body.dest))
            if not dest.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            _, stdout, _ = ssh.exec_command(f"mkdir -p {shlex.quote(dest)}")
            stdout.channel.recv_exit_status()
            for rel in body.paths:
                src = os.path.normpath(os.path.join(remote_base, rel))
                if not src.startswith(remote_base):
                    continue
                dst = os.path.join(dest, os.path.basename(src))
                _, stdout, stderr = ssh.exec_command(f"cp -r {shlex.quote(src)} {shlex.quote(dst)}")
                stdout.channel.recv_exit_status()
                copied.append(rel)
            sftp.close(); ssh.close()
            return {"copied": copied, "to": body.dest}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP copy error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    base_resolved = base.resolve()
    dest = (base / body.dest).resolve()
    if not str(dest).startswith(str(base_resolved)):
        raise HTTPException(403, "path traversal")
    dest.mkdir(parents=True, exist_ok=True)
    for rel in body.paths:
        src = (base / rel).resolve()
        if not str(src).startswith(str(base_resolved)):
            continue
        if not src.exists():
            continue
        dst = dest / src.name
        if src.is_dir():
            _shutil.copytree(str(src), str(dst))
        else:
            _shutil.copy2(str(src), str(dst))
        copied.append(rel)
    return {"copied": copied, "to": body.dest}


@app.get("/api/files/{project}/search")
async def search_files(project: str, q: str, path: str = ""):
    results = []
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)
            search_root = os.path.normpath(os.path.join(remote_base, path)) if path else remote_base
            if not search_root.startswith(remote_base):
                raise HTTPException(403, "path traversal")
            # Use find with -name, skipping hidden dirs
            find_cmd = (
                f"find {shlex.quote(search_root)} "
                f"-not \\( -name '.*' -prune \\) "
                f"-name {shlex.quote(q)} 2>/dev/null | head -100"
            )
            _, stdout, _ = ssh.exec_command(find_cmd)
            stdout.channel.recv_exit_status()
            for line in stdout.read().decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                rel = os.path.relpath(line, remote_base)
                try:
                    stat = sftp.stat(line)
                    import stat as stat_mod
                    kind = "dir" if stat_mod.S_ISDIR(stat.st_mode) else "file"
                except Exception:
                    kind = "file"
                results.append({"name": os.path.basename(line), "path": rel, "type": kind})
                if len(results) >= 100:
                    break
            sftp.close(); ssh.close()
            return {"results": results, "query": q}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP search error: {str(e)}")
    # Local
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    base_resolved = base.resolve()
    search_root = (base / path).resolve() if path else base_resolved
    if not str(search_root).startswith(str(base_resolved)):
        raise HTTPException(403, "path traversal")
    for dirpath, dirnames, filenames in os.walk(str(search_root)):
        # Skip hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in dirnames:
            if fnmatch.fnmatch(name, q) or q.lower() in name.lower():
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, str(base_resolved))
                results.append({"name": name, "path": rel, "type": "dir"})
                if len(results) >= 100:
                    return {"results": results, "query": q}
        for name in filenames:
            if fnmatch.fnmatch(name, q) or q.lower() in name.lower():
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, str(base_resolved))
                results.append({"name": name, "path": rel, "type": "file"})
                if len(results) >= 100:
                    return {"results": results, "query": q}
    return {"results": results, "query": q}


@app.get("/api/files/{project}/zip-download")
async def zip_download(project: str, paths: str):
    from starlette.responses import StreamingResponse as _SR
    path_list = [p.strip() for p in paths.split(",") if p.strip()]
    if not path_list:
        raise HTTPException(400, "no paths provided")
    buf = io.BytesIO()
    if _is_ssh_project(project):
        try:
            ssh, sftp = get_sftp(project)
            remote_base = _get_remote_base(project)

            def _sftp_add(zf: zipfile.ZipFile, remote_path: str, arc_base: str):
                import stat as stat_mod
                try:
                    st = sftp.stat(remote_path)
                except Exception:
                    return
                if stat_mod.S_ISDIR(st.st_mode):
                    for entry in sftp.listdir_attr(remote_path):
                        if entry.filename.startswith("."):
                            continue
                        _sftp_add(zf, os.path.join(remote_path, entry.filename),
                                  os.path.join(arc_base, entry.filename))
                else:
                    with sftp.open(remote_path, "rb") as f:
                        data = f.read()
                    zf.writestr(arc_base, data)

            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for rel in path_list:
                    target = os.path.normpath(os.path.join(remote_base, rel))
                    if not target.startswith(remote_base):
                        continue
                    _sftp_add(zf, target, os.path.basename(target))
            sftp.close(); ssh.close()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"SFTP zip error: {str(e)}")
    else:
        # Local
        base = _get_base(project)
        if not base.exists():
            raise HTTPException(404, f"'{project}' not found")
        base_resolved = base.resolve()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in path_list:
                target = (base / rel).resolve()
                if not str(target).startswith(str(base_resolved)):
                    continue
                if not target.exists():
                    continue
                if target.is_dir():
                    for dirpath, dirnames, filenames in os.walk(str(target)):
                        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                        for fname in filenames:
                            if fname.startswith("."):
                                continue
                            full = os.path.join(dirpath, fname)
                            arc = os.path.relpath(full, str(base_resolved))
                            zf.write(full, arc)
                else:
                    arc = os.path.relpath(str(target), str(base_resolved))
                    zf.write(str(target), arc)
    buf.seek(0)
    zip_name = f"{os.path.basename(path_list[0])}.zip" if len(path_list) == 1 else "download.zip"
    return _SR(buf, media_type="application/zip",
               headers={"Content-Disposition": f'attachment; filename="{zip_name}"'})


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
                ssh.exec_command(f"mkdir -p {shlex.quote(parent)}")
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

CHANNEL_REGISTRY = _HOME / "projects" / ".channel-registry.json"
CHANNELS_DIR = _HOME / "channels"
CHANNEL_KEY_FILE = Path.home() / ".config" / "oopsbox" / "channel.key"


def _get_channel_key() -> str:
    """Get or create the channel encryption key."""
    if CHANNEL_KEY_FILE.exists():
        return CHANNEL_KEY_FILE.read_text().strip()
    CHANNEL_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    import secrets
    key = secrets.token_hex(32)
    CHANNEL_KEY_FILE.write_text(key)
    os.chmod(str(CHANNEL_KEY_FILE), 0o600)
    return key


def _encrypt_token(token: str) -> str:
    """Encrypt a token with AES-256-CBC via openssl."""
    if not token:
        return ""
    key = _get_channel_key()
    r = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-a", "-A", "-salt", "-pbkdf2", "-pass", f"pass:{key}"],
        input=token, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def _decrypt_token(encrypted: str) -> str:
    """Decrypt a token encrypted with _encrypt_token."""
    if not encrypted:
        return ""
    key = _get_channel_key()
    r = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-a", "-A", "-d", "-salt", "-pbkdf2", "-pass", f"pass:{key}"],
        input=encrypted, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


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
    return {"name": name, "status": status, "workdir": meta.get("workdir", str(_HOME)),
            "skip_permissions": meta.get("skip_permissions", False),
            "has_api_key": bool(meta.get("api_key_enc")),
            "anthropic_base_url": meta.get("anthropic_base_url", "")}


@app.get("/api/channels")
async def list_channels():
    reg = load_channels()
    return {"channels": [get_channel_status(n) for n in reg]}


class ChannelCreateReq(BaseModel):
    name: str
    workdir: str = ""
    skip_permissions: bool = False
    telegram_token: str = ""
    api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None

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
    workdir = body.workdir if body.workdir and body.workdir != str(_HOME) else str(chan_dir)
    entry = {
        "workdir": workdir,
        "skip_permissions": body.skip_permissions,
        "telegram_token_enc": _encrypt_token(body.telegram_token),
    }
    if body.api_key:
        entry["api_key_enc"] = _encrypt_token(body.api_key)
    if body.anthropic_base_url:
        entry["anthropic_base_url"] = body.anthropic_base_url
    reg[body.name] = entry
    save_channels(reg)
    return get_channel_status(body.name)


class ChannelUpdateReq(BaseModel):
    workdir: Optional[str] = None
    skip_permissions: Optional[bool] = None
    api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None


@app.put("/api/channels/{name}")
async def update_channel(name: str, body: ChannelUpdateReq):
    reg = load_channels()
    if name not in reg:
        raise HTTPException(404, f"Channel '{name}' not found")
    if body.workdir is not None:
        reg[name]["workdir"] = body.workdir
    if body.skip_permissions is not None:
        reg[name]["skip_permissions"] = body.skip_permissions
    # Encrypt API key if provided; empty string clears it
    if body.api_key is not None:
        if body.api_key:
            reg[name]["api_key_enc"] = _encrypt_token(body.api_key)
        else:
            reg[name].pop("api_key_enc", None)
    # ANTHROPIC_BASE_URL for LiteLLM proxy; empty string clears it
    if body.anthropic_base_url is not None:
        if body.anthropic_base_url:
            reg[name]["anthropic_base_url"] = body.anthropic_base_url
        else:
            reg[name].pop("anthropic_base_url", None)
    save_channels(reg)
    return get_channel_status(name)


@app.get("/api/channels/{name}/settings")
async def get_channel_settings(name: str):
    reg = load_channels()
    if name not in reg:
        raise HTTPException(404, f"Channel '{name}' not found")
    meta = reg[name]
    safe = {k: v for k, v in meta.items() if k not in ("telegram_token_enc", "api_key_enc")}
    safe["has_api_key"] = bool(meta.get("api_key_enc"))
    return safe


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

