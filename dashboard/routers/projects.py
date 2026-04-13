# dashboard/routers/projects.py
import json, os, re, subprocess, shutil, signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

router = APIRouter(prefix="/api/projects", tags=["projects"])

_projects_root = Path.home() / "projects"
_bin_dir = Path.home() / "bin"


def set_projects_root(path: Path):
    global _projects_root
    _projects_root = path


def _registry_path() -> Path:
    return _projects_root / ".project-registry.json"


def _load_registry() -> dict:
    p = _registry_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_registry(data: dict):
    _projects_root.mkdir(parents=True, exist_ok=True)
    _registry_path().write_text(json.dumps(data, indent=2))


def _is_running(name: str) -> bool:
    pid_file = Path(f"/tmp/oopsbox-{name}") / "ttyd.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def _active_window(name: str) -> str | None:
    result = subprocess.run(
        ["tmux", "display-message", "-t", f"oopsbox-{name}", "-p", "#W"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def _runtime_info(name: str) -> dict:
    pid_dir = Path(f"/tmp/oopsbox-{name}")
    port_file = pid_dir / "ttyd.port"
    port = int(port_file.read_text().strip()) if port_file.exists() else None
    running = _is_running(name)
    return {
        "running": running,
        "ttyd_port": port,
        "active_window": _active_window(name) if running else None,
    }


NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


class CreateProjectRequest(BaseModel):
    name: str
    type: Literal["local", "ssh"] = "local"
    ssh_host: Optional[str] = None
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    remote_path: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if not NAME_RE.match(v):
            raise ValueError("Name must start with alphanumeric; allowed: letters, numbers, . _ -")
        return v


@router.get("")
def list_projects():
    registry = _load_registry()
    result = []
    for name, meta in registry.items():
        entry = dict(meta)
        entry.update(_runtime_info(name))
        result.append(entry)
    return sorted(result, key=lambda p: p["name"])


@router.post("")
def create_project(req: CreateProjectRequest):
    registry = _load_registry()
    if req.name in registry:
        raise HTTPException(status_code=400, detail=f"Project '{req.name}' already exists")

    project_dir = _projects_root / req.name
    if project_dir.exists():
        raise HTTPException(status_code=400, detail="Project directory already exists")

    project_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)

    if req.type == "local":
        (project_dir / "CLAUDE.md").write_text(f"# Project: {req.name}\n\nLocal OopsBox project.\n")
    else:
        default_path = f"/home/{req.ssh_user or 'user'}"
        (project_dir / "CLAUDE.md").write_text(
            f"# Project: {req.name} (SSH Remote)\n\n"
            f"Host: {req.ssh_host}\nUser: {req.ssh_user}\nPath: {req.remote_path or default_path}\n"
        )

    meta = {
        "name": req.name,
        "type": req.type,
        "path": str(project_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if req.type == "ssh":
        meta.update({
            "ssh_host": req.ssh_host,
            "ssh_port": req.ssh_port,
            "ssh_user": req.ssh_user,
            "remote_path": req.remote_path or f"/home/{req.ssh_user}",
        })
        if req.ssh_password:
            meta["ssh_password"] = req.ssh_password
        if req.ssh_key_path:
            meta["ssh_key_path"] = req.ssh_key_path

    registry[req.name] = meta
    _save_registry(registry)
    return meta


@router.get("/{name}")
def get_project(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = dict(registry[name])
    entry.update(_runtime_info(name))
    return entry


@router.delete("/{name}")
def delete_project(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")

    if _is_running(name):
        _stop_project(name)

    project_dir = Path(registry[name]["path"]).resolve()
    if not project_dir.is_relative_to(_projects_root.resolve()):
        raise HTTPException(status_code=500, detail="Project path is outside projects root")
    if project_dir.exists():
        shutil.rmtree(project_dir)

    del registry[name]
    _save_registry(registry)
    return {"ok": True}


def _stop_project(name: str):
    script = _bin_dir / "project-stop.sh"
    if script.exists():
        subprocess.run([str(script), name], capture_output=True)


class UpdateProjectRequest(BaseModel):
    ssh_host: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    remote_path: Optional[str] = None

@router.put("/{name}")
def update_project(name: str, req: UpdateProjectRequest):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    meta = registry[name]
    if req.ssh_host is not None: meta["ssh_host"] = req.ssh_host
    if req.ssh_port is not None: meta["ssh_port"] = req.ssh_port
    if req.ssh_user is not None: meta["ssh_user"] = req.ssh_user
    if req.ssh_password is not None: meta["ssh_password"] = req.ssh_password
    if req.ssh_key_path is not None: meta["ssh_key_path"] = req.ssh_key_path
    if req.remote_path is not None: meta["remote_path"] = req.remote_path
    registry[name] = meta
    _save_registry(registry)
    return meta


@router.post("/{name}/setup-ssh-key")
def setup_ssh_key(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    meta = registry[name]
    if meta.get("type") != "ssh":
        raise HTTPException(status_code=400, detail="Not an SSH project")

    host = meta.get("ssh_host")
    port = meta.get("ssh_port", 22)
    user = meta.get("ssh_user")
    password = meta.get("ssh_password")

    if not all([host, user]):
        raise HTTPException(status_code=400, detail="Missing SSH host or user")
    if not password:
        raise HTTPException(status_code=400, detail="Password required to install SSH key (already using key auth?)")

    # Generate keypair in persistent config volume so it survives container rebuilds
    ssh_dir = Path.home() / ".config" / "oopsbox" / "ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key_path = ssh_dir / "oopsbox_id_rsa"
    pub_path = Path(str(key_path) + ".pub")

    if not key_path.exists():
        result = subprocess.run(
            ["ssh-keygen", "-t", "rsa", "-b", "4096", "-N", "", "-f", str(key_path)],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail="Failed to generate SSH key: " + result.stderr)
        key_path.chmod(0o600)

    # Copy public key to remote server
    result = subprocess.run(
        ["sshpass", "-p", password,
         "ssh-copy-id",
         "-i", str(pub_path),
         "-p", str(port),
         "-o", "StrictHostKeyChecking=no",
         f"{user}@{host}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="ssh-copy-id failed: " + (result.stderr or result.stdout).strip())

    # Update registry: use key auth, remove password
    meta["ssh_key_path"] = str(key_path)
    meta.pop("ssh_password", None)
    registry[name] = meta
    _save_registry(registry)
    return {"ok": True, "key_path": str(key_path)}


@router.post("/{name}/start")
def start_project(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    if _is_running(name):
        return {"ok": True, "already_running": True}
    script = _bin_dir / "project-start.sh"
    result = subprocess.run([str(script), name], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr.strip())
    return {"ok": True}


@router.post("/{name}/stop")
def stop_project(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    _stop_project(name)
    return {"ok": True}


@router.get("/{name}/status")
def project_status(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    return {**registry[name], **_runtime_info(name)}


class SendKeysRequest(BaseModel):
    keys: str


@router.post("/{name}/send-keys")
def send_keys(name: str, req: SendKeysRequest):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    result = subprocess.run(
        ["tmux", "send-keys", "-t", f"oopsbox-{name}", req.keys, ""],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Terminal not available")
    return {"ok": True}


class SendTextRequest(BaseModel):
    text: str


@router.post("/{name}/send-text")
def send_text(name: str, req: SendTextRequest):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    result = subprocess.run(
        ["tmux", "send-keys", "-t", f"oopsbox-{name}", req.text, "Enter"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Terminal not available")
    return {"ok": True}


class MouseRequest(BaseModel):
    enabled: bool

@router.post("/{name}/mouse")
def set_mouse(name: str, req: MouseRequest):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    val = "on" if req.enabled else "off"
    result = subprocess.run(
        ["tmux", "set-option", "-t", f"oopsbox-{name}", "mouse", val],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="tmux not available")
    return {"mouse": val}


class SelectWindowRequest(BaseModel):
    window: str


@router.post("/{name}/select-window")
def select_window(name: str, req: SelectWindowRequest):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    result = subprocess.run(
        ["tmux", "select-window", "-t", f"oopsbox-{name}:{req.window}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail="Window not found")
    return {"ok": True}


@router.get("/{name}/clipboard")
def get_clipboard(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    result = subprocess.run(
        ["tmux", "show-buffer", "-t", f"oopsbox-{name}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise HTTPException(status_code=404, detail="Clipboard empty")
    return {"text": result.stdout}
