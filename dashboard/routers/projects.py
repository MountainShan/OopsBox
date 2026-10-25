# dashboard/routers/projects.py
import asyncio, json, logging, os, re, shlex, subprocess, shutil, signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..agents.launch import ssh_target
from ..agents.manager import manager
from ..classification import DEFAULT_CLASSIFICATION, agent_access, is_valid
from ..memory import ensure_memory, list_files, read_file
from .. import terminals

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/projects", tags=["projects"])

_projects_root = Path.home() / "projects"
# OOPSBOX_BIN_DIR lets a second checkout (a trial instance) use its own scripts.
_bin_dir = Path(os.environ.get("OOPSBOX_BIN_DIR") or Path.home() / "bin")


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


# The terminal that is not a project's: a shell in the projects folder.
# Shells in ~/projects that belong to no project: _t1, _t2 … one per
# Terminal tab, made by the ›_ button; _global is the single one older
# versions had.
GLOBAL_TERMINAL = "_global"
_SHELL = re.compile(r"_t[1-9][0-9]{0,3}")


def is_shell(name: str) -> bool:
    return name == GLOBAL_TERMINAL or bool(_SHELL.fullmatch(name))


def _terminal_target(name: str) -> None:
    """404 unless name is a project or a shell terminal."""
    if not is_shell(name) and name not in _load_registry():
        raise HTTPException(status_code=404, detail="Project not found")


def next_shell(running: list[str]) -> str:
    """The lowest _tN not in use."""
    n = 1
    while f"_t{n}" in running:
        n += 1
    return f"_t{n}"


def _is_running(name: str) -> bool:
    return terminals.is_running(name)


def _runtime_info(name: str) -> dict:
    pid_dir = Path(f"/tmp/oopsbox-{name}")
    port_file = pid_dir / "ttyd.port"
    port = int(port_file.read_text().strip()) if port_file.exists() else None
    running = _is_running(name)
    agents = manager.running(name)
    return {
        # a project counts as running if its terminal or any chat agent is up
        "running": running or bool(agents),
        "terminal_running": running,
        "agents_running": agents,
        "agents": manager.summary(name),
        "ttyd_port": port,
    }


NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$')


def _classification_of(meta: dict) -> str:
    """Classification of a project, defaulting only when the key is absent.

    A present-but-invalid value (empty string, null, a typo) is returned as-is
    so agent_access() applies its strictest-per-agent fallback. Treating falsy
    values as "missing" would promote a corrupted entry to `personal`, which
    opens the cloud agents — the exact failure the fallback exists to stop.
    """
    if "classification" not in meta:
        return DEFAULT_CLASSIFICATION
    value = meta["classification"]
    if value is None:
        return ""
    return str(value)


class CreateProjectRequest(BaseModel):
    name: str
    type: Literal["local", "ssh"] = "local"
    ssh_host: Optional[str] = None
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    remote_path: Optional[str] = None
    classification: str = DEFAULT_CLASSIFICATION

    @field_validator("classification")
    @classmethod
    def validate_classification(cls, v):
        if not is_valid(v):
            raise ValueError(
                "classification must be one of: personal, work, work-restricted"
            )
        return v

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
        entry["classification"] = _classification_of(entry)
        entry.update(_runtime_info(name))
        result.append(entry)
    return sorted(result, key=lambda p: p["name"])


@router.post("")
def create_project(req: CreateProjectRequest):
    registry = _load_registry()
    if req.name in registry:
        raise HTTPException(status_code=400, detail=f"Project '{req.name}' already exists")

    # A folder left by a project removed without its files is taken up
    # again as it is: nothing in it is replaced.
    project_dir = _projects_root / req.name
    adopted = project_dir.is_dir()
    project_dir.mkdir(parents=True, exist_ok=True)
    if not (project_dir / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)

    if (project_dir / "CLAUDE.md").exists():
        pass
    elif req.type == "local":
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
        "classification": req.classification,
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

    ensure_memory(project_dir)

    return {**meta, "adopted": adopted}


@router.get("/{name}")
def get_project(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    entry = dict(registry[name])
    entry["classification"] = _classification_of(entry)
    entry.update(_runtime_info(name))
    return entry


@router.delete("/{name}")
async def delete_project(name: str, delete_files: bool = False):
    """Remove a project from OopsBox. Its folder and chat history stay
    (creating a project of the same name takes them up again) unless
    delete_files is set."""
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")

    project_dir = Path(registry[name]["path"]).resolve()
    if delete_files and not project_dir.is_relative_to(_projects_root.resolve()):
        raise HTTPException(status_code=500, detail="Project path is outside projects root")

    if _is_running(name):
        _stop_project(name)
    if delete_files:
        await manager.forget_project(name)
        if project_dir.exists():
            shutil.rmtree(project_dir)
    else:
        await manager.drop_project(name)

    del registry[name]
    _save_registry(registry)
    return {"ok": True}


def _stop_project(name: str):
    terminals.stop(name)
    # a tmux session left by an OopsBox from before the terminal daemons
    subprocess.run(["tmux", "kill-session", "-t", f"oopsbox-{name}"], capture_output=True)


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
    response = dict(meta)
    response["classification"] = _classification_of(meta)
    return response


class SetClassificationRequest(BaseModel):
    classification: str


@router.put("/{name}/classification")
async def set_classification(name: str, req: SetClassificationRequest):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    if not is_valid(req.classification):
        raise HTTPException(
            status_code=400,
            detail="classification must be one of: personal, work, work-restricted",
        )
    registry[name]["classification"] = req.classification
    _save_registry(registry)
    # an agent the new classification blocks may not keep working on it
    stopped = []
    for agent, state in agent_access(req.classification).items():
        session = manager.peek(name, agent)
        if state == "blocked" and session is not None and session.alive:
            await session.stop()
            stopped.append(agent)
    return {"name": name, "classification": req.classification, "stopped": stopped}


@router.get("/{name}/memory")
def get_memory_files(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    project_dir = Path(registry[name]["path"])
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail="Project directory not found")
    ensure_memory(project_dir)
    return {"files": list_files(project_dir)}


@router.get("/{name}/memory/content")
def get_memory_content(name: str, path: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    project_dir = Path(registry[name]["path"])
    try:
        content = read_file(project_dir, path)
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="File is not valid UTF-8 text")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid path")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    return {"path": path, "content": content}


@router.get("/{name}/agents")
def get_agents(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    classification = _classification_of(registry[name])
    return {"classification": classification, "agents": agent_access(classification)}


@router.post("/{name}/start")
async def start_project(name: str):
    """Start the project's agents: every one its classification makes
    available, plus the last one used even if it is only reachable on request."""
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    meta = registry[name]
    access = agent_access(_classification_of(meta))
    agents = [a for a, state in access.items() if state == "available"]
    default = manager.default_agent(name, access)
    if default and default not in agents:
        agents.append(default)
    if not agents:
        raise HTTPException(status_code=403, detail="No agent is allowed on this project")
    sessions = [manager.get(meta, a) for a in agents]
    await asyncio.gather(*(s.start() for s in sessions if not s.alive))
    failed = {s.agent: s.error for s in sessions if s.status == "error"}
    return {"ok": not failed, "agents": agents, "failed": failed}


async def restore_agents() -> None:
    """At startup: bring back the agents that were running when OopsBox
    stopped, so an active project's agents are waiting again."""
    for name, meta in _load_registry().items():
        access = agent_access(_classification_of(meta))
        allowed = [a for a, state in access.items() if state != "blocked"]
        try:
            started = await manager.restore(meta, allowed)
        except Exception:
            log.exception("restoring agents for %s failed", name)
            continue
        if started:
            log.info("restored %s for %s", ", ".join(started), name)


@router.post("/{name}/start-terminal")
def start_terminal(name: str):
    """A browser terminal for manual work: a shell daemon (ttyd + dtach)
    that keeps running when the page closes and when OopsBox restarts."""
    _terminal_target(name)
    if _is_running(name):
        return {"ok": True, "already_running": True}
    if is_shell(name):
        workdir, first, env = _projects_root, None, None
    else:
        meta = _load_registry()[name]
        workdir, first, env = Path(meta["path"]), *_remote_shell(meta)
    try:
        terminals.start(name, workdir, first, env)
    except terminals.TerminalError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


def _remote_shell(meta: dict) -> tuple[Optional[str], Optional[dict]]:
    """An SSH project's terminal opens on the remote host, in its folder;
    leaving it drops to a local shell. The password travels in SSHPASS."""
    if meta.get("type") != "ssh":
        return None, None
    remote = meta.get("remote_path") or "~"
    cd = "cd ~" if remote == "~" else ("cd ~/" + shlex.quote(remote[2:]) if remote.startswith("~/") else "cd " + shlex.quote(remote))
    try:
        dest, port, key = ssh_target(meta.get("ssh_user"), meta.get("ssh_host"),
                                     meta.get("ssh_port"), meta.get("ssh_key_path"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ssh = ["ssh", "-t", "-p", str(port), "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        ssh += ["-i", key]
    ssh += ["--", dest, f"{cd} && exec $SHELL -l"]
    if meta.get("ssh_password"):
        return "sshpass -e " + shlex.join(ssh), {"SSHPASS": meta["ssh_password"]}
    return shlex.join(ssh), None


@router.post("/{name}/stop-terminal")
def stop_terminal(name: str):
    _terminal_target(name)
    _stop_project(name)
    return {"ok": True}


@router.post("/{name}/stop")
async def stop_project(name: str):
    """Stop every chat agent and the legacy terminal session, if any."""
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    await manager.stop_project(name)
    _stop_project(name)
    return {"ok": True}


@router.get("/{name}/status")
def project_status(name: str):
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    return {**registry[name], **_runtime_info(name)}



shells = APIRouter(prefix="/api/terminals", tags=["terminals"])


@shells.get("")
def list_shells():
    """The shell terminals that are running, for their tabs."""
    names = [n for n in terminals.running() if is_shell(n)]
    return {"terminals": sorted(names, key=lambda n: (n != GLOBAL_TERMINAL, int(n[2:]) if n != GLOBAL_TERMINAL else 0))}


@shells.post("")
def new_shell():
    """Start another shell in ~/projects; its tab opens on it."""
    name = next_shell(terminals.running())
    try:
        terminals.start(name, _projects_root)
    except terminals.TerminalError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"name": name}
