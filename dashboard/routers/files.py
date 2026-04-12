import os, shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/files", tags=["files"])

_projects_root = Path.home() / "projects"


def set_projects_root(path: Path):
    global _projects_root
    _projects_root = path


def _resolve(project: str, rel_path: str) -> Path:
    root = (_projects_root / project).resolve()
    target = (root / rel_path).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")
    return target


def _project_root(project: str) -> Path:
    root = (_projects_root / project).resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"Project '{project}' not found")
    return root


def _file_entry(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.relative_to(root)),
        "is_dir": path.is_dir(),
        "size": stat.st_size if not path.is_dir() else 0,
        "modified": stat.st_mtime,
    }


@router.get("/{project}")
def list_files(project: str, path: str = ""):
    root = _project_root(project)
    target = _resolve(project, path) if path else root
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    files = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    return {"files": [_file_entry(f, root) for f in files]}


@router.get("/{project}/read")
def read_file(project: str, path: str):
    target = _resolve(project, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = target.read_text(errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"content": content, "path": path}


@router.get("/{project}/download")
def download_file(project: str, path: str):
    target = _resolve(project, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=target, filename=target.name)


class PathRequest(BaseModel):
    path: str

class RenameRequest(BaseModel):
    path: str
    new_name: str

class WriteRequest(BaseModel):
    path: str
    content: str


@router.post("/{project}/rename")
def rename(project: str, req: RenameRequest):
    target = _resolve(project, req.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    new_path = target.parent / req.new_name
    if new_path.exists():
        raise HTTPException(status_code=400, detail="Destination already exists")
    target.rename(new_path)
    return {"ok": True}


@router.post("/{project}/delete")
def delete(project: str, req: PathRequest):
    target = _resolve(project, req.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


@router.post("/{project}/mkdir")
def mkdir(project: str, req: PathRequest):
    target = _resolve(project, req.path)
    if target.exists():
        raise HTTPException(status_code=400, detail="Already exists")
    target.mkdir(parents=True)
    return {"ok": True}


@router.put("/{project}/write")
def write_file(project: str, req: WriteRequest):
    target = _resolve(project, req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content)
    return {"ok": True}


@router.post("/{project}/upload")
async def upload(project: str, path: str = "", file: UploadFile = File(...)):
    root = _project_root(project)
    dest_dir = _resolve(project, path) if path else root
    dest = dest_dir / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"ok": True, "name": file.filename}
