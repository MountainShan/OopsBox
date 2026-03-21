import json
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, field_validator
import re

app = FastAPI(title="RVCoder")

PROJECTS_DIR = Path.home() / "projects"
BIN_DIR      = Path.home() / "bin"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)


def project_names() -> list[str]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(p.name for p in PROJECTS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def get_status(name: str) -> dict:
    r = run([str(BIN_DIR / "project-status.sh"), name])
    if r.returncode != 0 or not r.stdout.strip():
        return {"name": name, "status": "idle",
                "code_port": None, "ttyd_port": None,
                "ttyd": False, "tmux": False}
    return json.loads(r.stdout)


# ── Project API ──────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects():
    return {"projects": [get_status(n) for n in project_names()]}


class CreateReq(BaseModel):
    name: str
    @field_validator("name")
    @classmethod
    def validate(cls, v):
        if not re.match(r'^[a-zA-Z0-9._-]{2,40}$', v):
            raise ValueError("2-40 chars, letters, numbers, dots, underscores, hyphens")
        return v


@app.post("/api/projects", status_code=201)
async def create_project(body: CreateReq):
    if (PROJECTS_DIR / body.name).exists():
        raise HTTPException(409, f"'{body.name}' already exists")
    r = run([str(BIN_DIR / "project-create.sh"), body.name])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr or r.stdout)
    return get_status(body.name)


@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    r = run([str(BIN_DIR / "project-delete.sh"), name])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
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
async def send_keys(name: str, keys: str = "C-c"):
    allowed = {"C-c", "C-z", "C-d", "C-\\"}
    if keys not in allowed:
        raise HTTPException(400, f"keys must be one of: {allowed}")
    if name == "_system":
        r = run(["tmux", "send-keys", "-t", "system", keys])
        if r.returncode != 0:
            raise HTTPException(500, r.stderr)
        return {"sent": keys, "session": "system"}
    if not (PROJECTS_DIR / name).exists():
        raise HTTPException(404, f"'{name}' not found")
    session = f"proj-{name}"
    r = run(["tmux", "send-keys", "-t", session, keys])
    if r.returncode != 0:
        raise HTTPException(500, r.stderr)
    return {"sent": keys, "session": session}


# ── Terminal Theme ───────────────────────────────────────────────────────────

THEME_CONF = Path.home() / ".config" / "ttyd-theme.conf"

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
    for name in project_names():
        run([str(BIN_DIR / "project-stop.sh"), name])
        run([str(BIN_DIR / "project-start.sh"), name])
    return {"theme": theme, "restarted": project_names()}


# ── System Stats ─────────────────────────────────────────────────────────────

@app.get("/api/system")
async def system_stats():
    import shutil
    r_cpu = run(["awk", "{print $1,$2,$3}", "/proc/loadavg"])
    load = r_cpu.stdout.strip() if r_cpu.returncode == 0 else "?"
    r_ncpu = run(["nproc"])
    ncpu = int(r_ncpu.stdout.strip()) if r_ncpu.returncode == 0 else 1
    r_stat = run(["head", "-1", "/proc/stat"])
    cpu_pct = 0
    if r_stat.returncode == 0:
        parts = r_stat.stdout.split()
        if len(parts) >= 8:
            user, nice, system, idle = int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
            total = user + nice + system + idle
            cpu_pct = round((total - idle) / total * 100, 1) if total > 0 else 0
    mem = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            mem[k.strip()] = int(v.strip().split()[0])
    ram_total = mem.get("MemTotal", 0) // 1024
    ram_avail = mem.get("MemAvailable", 0) // 1024
    ram_used = ram_total - ram_avail
    ram_pct = round(ram_used / ram_total * 100, 1) if ram_total > 0 else 0
    swap_total = mem.get("SwapTotal", 0) // 1024
    swap_free = mem.get("SwapFree", 0) // 1024
    swap_used = swap_total - swap_free
    swap_pct = round(swap_used / swap_total * 100, 1) if swap_total > 0 else 0
    disk = shutil.disk_usage("/")
    disk_total = disk.total // (1024**3)
    disk_used = disk.used // (1024**3)
    disk_pct = round(disk.used / disk.total * 100, 1)
    return {
        "cpu": {"percent": cpu_pct, "load": load, "cores": ncpu},
        "ram": {"used_mb": ram_used, "total_mb": ram_total, "percent": ram_pct},
        "swap": {"used_mb": swap_used, "total_mb": swap_total, "percent": swap_pct},
        "disk": {"used_gb": disk_used, "total_gb": disk_total, "percent": disk_pct},
    }


# ── File Browser API ─────────────────────────────────────────────────────────

def _get_base(project: str) -> Path:
    if project == "_system":
        return Path.home()
    return PROJECTS_DIR / project

@app.get("/api/files/{project}")
async def list_files(project: str, path: str = ""):
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
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "path": str(item.relative_to(base)),
            "size": item.stat().st_size if item.is_file() else None,
        })
    return {"type": "dir", "path": path, "entries": entries}


@app.get("/api/files/{project}/read")
async def read_file(project: str, path: str):
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

@app.post("/api/files/{project}/move")
async def move_file(project: str, path: str, body: MoveReq):
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

@app.put("/api/files/{project}/write")
async def write_file(project: str, path: str, body: SaveReq):
    base = _get_base(project)
    if not base.exists():
        raise HTTPException(404, f"'{project}' not found")
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(403, "path traversal")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content)
    return {"saved": path}


# ── Static Frontend ──────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="/opt/dashboard/static"), name="static")

@app.get("/")
async def root():
    return FileResponse("/opt/dashboard/static/index.html")
