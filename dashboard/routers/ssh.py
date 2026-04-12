# dashboard/routers/ssh.py
import io
import json
import stat
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import paramiko
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/ssh", tags=["ssh"])

_projects_root = Path.home() / "projects"


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


def _get_meta(project: str) -> dict:
    registry = _load_registry()
    if project not in registry:
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    meta = registry[project]
    if meta.get("type") != "ssh":
        raise HTTPException(status_code=400, detail=f"Project '{project}' is not an SSH project")
    return meta


def _resolve_remote(meta: dict, rel: str) -> str:
    """Construct absolute remote path from remote_path + rel, preventing traversal."""
    base = meta["remote_path"].rstrip("/")
    if not rel or rel in (".", ""):
        return base
    # Normalize: strip leading slashes to keep it relative
    rel = rel.lstrip("/")
    # Build candidate by simple join then resolve-style normalization
    parts = (base + "/" + rel).split("/")
    resolved_parts = []
    for p in parts:
        if p == "..":
            if resolved_parts:
                resolved_parts.pop()
        elif p and p != ".":
            resolved_parts.append(p)
    resolved = "/" + "/".join(resolved_parts)
    if not resolved.startswith(base):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    return resolved


def _open_sftp(meta: dict):
    """Open SSH connection and return (ssh_client, sftp_client)."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = dict(
        hostname=meta["ssh_host"],
        port=int(meta.get("ssh_port", 22)),
        username=meta["ssh_user"],
        timeout=15,
    )
    password = meta.get("ssh_password")
    key_path = meta.get("ssh_key_path")
    if password:
        connect_kwargs["password"] = password
        connect_kwargs["look_for_keys"] = False
        connect_kwargs["allow_agent"] = False
    elif key_path:
        connect_kwargs["key_filename"] = key_path
    ssh.connect(**connect_kwargs)
    sftp = ssh.open_sftp()
    return ssh, sftp


def _rel_path(meta: dict, abs_path: str) -> str:
    """Return path relative to remote_path."""
    base = meta["remote_path"].rstrip("/")
    if abs_path == base:
        return ""
    if abs_path.startswith(base + "/"):
        return abs_path[len(base) + 1:]
    return abs_path


def _file_entry(meta: dict, sftp: paramiko.SFTPClient, abs_path: str, attr) -> dict:
    is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
    return {
        "name": abs_path.split("/")[-1],
        "path": _rel_path(meta, abs_path),
        "is_dir": is_dir,
        "size": attr.st_size if not is_dir else 0,
        "modified": float(attr.st_mtime) if attr.st_mtime else 0.0,
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PathRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    path: str
    new_name: str


class WriteRequest(BaseModel):
    path: str
    content: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{project}")
def list_directory(project: str, path: str = ""):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, path)
    ssh, sftp = _open_sftp(meta)
    try:
        _sftp_makedirs(sftp, abs_path)
        attrs = sftp.listdir_attr(abs_path)
        entries = []
        for attr in attrs:
            child_path = abs_path.rstrip("/") + "/" + attr.filename
            is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
            entries.append({
                "name": attr.filename,
                "path": _rel_path(meta, child_path),
                "is_dir": is_dir,
                "size": attr.st_size if not is_dir else 0,
                "modified": float(attr.st_mtime) if attr.st_mtime else 0.0,
            })
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return {"files": entries}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()


@router.get("/{project}/read")
def read_file(project: str, path: str):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, path)
    ssh, sftp = _open_sftp(meta)
    try:
        buf = io.BytesIO()
        sftp.getfo(abs_path, buf)
        content = buf.getvalue().decode("utf-8", errors="replace")
        return {"content": content, "path": path}
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()


@router.get("/{project}/download")
def download_file(project: str, path: str):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, path)
    filename = abs_path.split("/")[-1]

    ssh, sftp = _open_sftp(meta)
    try:
        buf = io.BytesIO()
        sftp.getfo(abs_path, buf)
        buf.seek(0)
        data = buf.read()
    except FileNotFoundError:
        sftp.close()
        ssh.close()
        raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        sftp.close()
        ssh.close()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()

    encoded = quote(filename)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.post("/{project}/delete")
def delete(project: str, req: PathRequest):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, req.path)
    ssh, sftp = _open_sftp(meta)
    try:
        attr = sftp.stat(abs_path)
        is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
        if is_dir:
            _sftp_rmtree(sftp, abs_path)
        else:
            sftp.remove(abs_path)
        return {"ok": True}
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()


def _sftp_rmtree(sftp: paramiko.SFTPClient, path: str):
    """Recursively delete a remote directory."""
    for attr in sftp.listdir_attr(path):
        child = path.rstrip("/") + "/" + attr.filename
        if stat.S_ISDIR(attr.st_mode):
            _sftp_rmtree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(path)


@router.post("/{project}/rename")
def rename(project: str, req: RenameRequest):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, req.path)
    ssh, sftp = _open_sftp(meta)
    try:
        parent = abs_path.rsplit("/", 1)[0] if "/" in abs_path else ""
        new_abs = (parent + "/" + req.new_name) if parent else req.new_name
        # Ensure new name doesn't escape base
        new_rel = _rel_path(meta, new_abs)
        _resolve_remote(meta, new_rel)  # triggers traversal check via the resolved form
        sftp.rename(abs_path, new_abs)
        return {"ok": True}
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()


@router.post("/{project}/mkdir")
def mkdir(project: str, req: PathRequest):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, req.path)
    ssh, sftp = _open_sftp(meta)
    try:
        sftp.mkdir(abs_path)
        return {"ok": True}
    except HTTPException:
        raise
    except IOError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()


@router.put("/{project}/write")
def write_file(project: str, req: WriteRequest):
    meta = _get_meta(project)
    abs_path = _resolve_remote(meta, req.path)
    ssh, sftp = _open_sftp(meta)
    try:
        data = req.content.encode("utf-8")
        buf = io.BytesIO(data)
        # Ensure parent directory exists
        parent = abs_path.rsplit("/", 1)[0] if "/" in abs_path else None
        if parent:
            _sftp_makedirs(sftp, parent)
        sftp.putfo(buf, abs_path)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()


def _sftp_makedirs(sftp: paramiko.SFTPClient, path: str):
    """Create remote directory and parents if they don't exist."""
    parts = path.lstrip("/").split("/")
    current = ""
    for part in parts:
        current = current + "/" + part
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


@router.post("/{project}/upload")
async def upload(project: str, path: str = "", file: UploadFile = File(...)):
    meta = _get_meta(project)
    base = meta["remote_path"].rstrip("/")
    dest_dir = _resolve_remote(meta, path) if path else base
    dest_path = dest_dir.rstrip("/") + "/" + file.filename
    # Verify dest_path doesn't escape base
    if not dest_path.startswith(base):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    content = await file.read()
    ssh, sftp = _open_sftp(meta)
    try:
        buf = io.BytesIO(content)
        sftp.putfo(buf, dest_path)
        return {"ok": True, "name": file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        sftp.close()
        ssh.close()
