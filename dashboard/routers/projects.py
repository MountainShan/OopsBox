# dashboard/routers/projects.py
import asyncio, json, logging, os, re, shlex, subprocess, shutil, signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..agents import catalog
from ..agents.launch import ssh_target
from ..agents.manager import AGENT_ORDER, manager
from ..classification import DEFAULT_CLASSIFICATION, agent_access, is_valid, other_agent_access
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


def classification_ceiling(classification: str) -> dict[str, str]:
    """What a classification allows, for every agent OopsBox knows."""
    table = agent_access(classification)
    return {a: table.get(a) or other_agent_access(classification) for a in AGENT_ORDER}


def agent_access_of(meta: dict) -> dict[str, str]:
    """{agent: available | hidden | blocked} for a project, in the order its
    agents are shown.

    The classification is the ceiling; within it, the project's own choice
    (enabled_agents) decides. A chosen agent is simply available - choosing
    is the deliberate step a hidden one waits for - and one not chosen is
    blocked. A project from before the choice existed gets what its
    classification allows, as it always did.
    """
    ceiling = classification_ceiling(_classification_of(meta))
    chosen = meta.get("enabled_agents")
    if not isinstance(chosen, list):
        # experimental agents only ever by choice
        return {a: ceiling[a] if a in catalog.STABLE else "blocked" for a in AGENT_ORDER}
    out = {a: ("available" if ceiling.get(a, "blocked") != "blocked" else "blocked")
           for a in chosen if a in AGENT_ORDER}
    for a in AGENT_ORDER:
        out.setdefault(a, "blocked")
    return out


def _check_agents(agents: Optional[list[str]]) -> Optional[list[str]]:
    if agents is None:
        return None
    unknown = [a for a in agents if a not in AGENT_ORDER]
    if unknown:
        raise ValueError(f"unknown agent: {', '.join(unknown)}")
    agents = list(dict.fromkeys(agents))
    if not agents:
        raise ValueError("choose at least one agent")
    return agents


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
    # the agents this project uses, in order; None: whatever the classification allows
    agents: Optional[list[str]] = None

    @field_validator("agents")
    @classmethod
    def validate_agents(cls, v):
        return _check_agents(v)

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
        entry["agent_access"] = agent_access_of(entry)
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
    if req.agents is not None:
        meta["enabled_agents"] = req.agents
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
    entry["agent_access"] = agent_access_of(entry)
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
    if registry[name].get("type") == "ssh":
        await _release_remote(registry[name])
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

async def _release_remote(meta: dict) -> None:
    """Unmount an SSH project's folder and close its connection."""
    from ..agents import remote
    from ..agents.session import ProjectInfo
    try:
        await remote.release(meta["name"], ProjectInfo.from_registry(meta))
    except Exception:
        log.exception("releasing the remote folder of %s failed", meta["name"])


@router.put("/{name}")
async def update_project(name: str, req: UpdateProjectRequest):
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
    if meta.get("type") == "ssh":
        # agents connect again, with the new settings, when they next start
        await manager.stop_project(name)
        await _release_remote(meta)
    response = dict(meta)
    response["classification"] = _classification_of(meta)
    return response


_MAX_FOLDERS = 20


class SetFoldersRequest(BaseModel):
    folders: list[str]

    @field_validator("folders")
    @classmethod
    def validate_folders(cls, v):
        out = []
        for raw in v:
            d = (raw or "").strip()
            if not d:
                continue
            p = Path(os.path.expanduser(d))
            if not p.is_absolute():
                raise ValueError(f"not an absolute path: {d}")
            p = p.resolve()
            if not p.is_dir():
                raise ValueError(f"no such folder: {d}")
            if str(p) not in out:
                out.append(str(p))
        if len(out) > _MAX_FOLDERS:
            raise ValueError(f"at most {_MAX_FOLDERS} folders")
        return out


@router.put("/{name}/folders")
async def set_folders(name: str, req: SetFoldersRequest):
    """More folders the project's agents may work in (on this host). Agents
    running now start again so they get them."""
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    if req.folders:
        registry[name]["extra_dirs"] = req.folders
    else:
        registry[name].pop("extra_dirs", None)
    _save_registry(registry)
    restarted = []
    for agent in AGENT_ORDER:
        session = manager.peek(name, agent)
        if session is not None and session.alive:
            await session.stop()
            await manager.get(registry[name], agent).start()  # get() picks up the new folders
            restarted.append(agent)
    return {"name": name, "folders": req.folders, "restarted": restarted}


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
    stopped = await _stop_blocked(name, registry[name])
    return {"name": name, "classification": req.classification, "stopped": stopped}


async def _stop_blocked(name: str, meta: dict) -> list[str]:
    stopped = []
    for agent, state in agent_access_of(meta).items():
        session = manager.peek(name, agent)
        if state == "blocked" and session is not None and session.alive:
            await session.stop()
            stopped.append(agent)
    return stopped


class SetAgentsRequest(BaseModel):
    agents: list[str]

    @field_validator("agents")
    @classmethod
    def validate_agents(cls, v):
        return _check_agents(v)


@router.put("/{name}/agents")
async def set_agents(name: str, req: SetAgentsRequest):
    """Choose the agents a project uses; one taken off is stopped (its chats stay)."""
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    registry[name]["enabled_agents"] = req.agents
    _save_registry(registry)
    stopped = await _stop_blocked(name, registry[name])
    return {"name": name, "agents": req.agents, "agent_access": agent_access_of(registry[name]),
            "stopped": stopped}


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
    return {"classification": _classification_of(registry[name]), "agents": agent_access_of(registry[name])}


@router.post("/{name}/start")
async def start_project(name: str):
    """Start the project's agents: every one its classification makes
    available, plus the last one used even if it is only reachable on request."""
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    meta = registry[name]
    access = agent_access_of(meta)
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


# The app's event loop (set at startup), for work started from a sync route.
main_loop: Optional[asyncio.AbstractEventLoop] = None


def _detect_soon(registry: dict, only: Optional[str] = None) -> None:
    """Find out in the background which agents a machine has."""
    async def run():
        try:
            await catalog.detect(registry, only)
        except Exception:
            log.exception("agent detection failed")
    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:  # a sync route runs in a worker thread
        if main_loop is not None and main_loop.is_running():
            asyncio.run_coroutine_threadsafe(run(), main_loop)


# ── Agents a machine has ───────────────────────────
agents_router = APIRouter(prefix="/api/agents", tags=["agents"])


def _hosts_view(registry: dict) -> list[dict]:
    found = catalog.load()
    out = []
    for key, meta in catalog.hosts(registry).items():
        r = found.get(key) or {"agents": catalog.parse("")}
        out.append({"key": key, "label": "This server" if key == catalog.LOCAL else key.removesuffix(":22"),
                    "at": r.get("at"), "error": r.get("error"), "agents": r.get("agents", {})})
    return out


@agents_router.get("")
def list_agents():
    """The agents OopsBox knows, and what each machine projects use has installed."""
    return _agents_view(_load_registry())


class DetectRequest(BaseModel):
    host: Optional[str] = None


@agents_router.post("/detect")
async def detect_agents(req: DetectRequest):
    registry = _load_registry()
    await catalog.detect(registry, req.host)
    return _agents_view(registry)


def _agents_view(registry: dict) -> dict:
    from ..classification import VALID_CLASSIFICATIONS
    return {"catalog": catalog.catalog(), "hosts": _hosts_view(registry),
            # what each classification allows, for choosing within it
            "classifications": {c: classification_ceiling(c) for c in VALID_CLASSIFICATIONS}}


async def restore_agents() -> None:
    """At startup: bring back the agents that were running when OopsBox
    stopped, so an active project's agents are waiting again."""
    for name, meta in _load_registry().items():
        access = agent_access_of(meta)
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
