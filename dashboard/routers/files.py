import os, shutil
from pathlib import Path
from typing import Optional

import hashlib
import pathlib
import shutil
import subprocess
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import uploads

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
def download_file(project: str, path: str, inline: bool = False):
    target = _resolve(project, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # An attachment disposition makes the browser treat an iframe load as a
    # navigation and save the file instead of displaying it, so anything the
    # viewer renders in place -- a PDF, an image -- has to ask for inline.
    return FileResponse(
        path=target,
        filename=target.name,
        content_disposition_type="inline" if inline else "attachment",
    )


# ── Office document preview ───────────────────────────
# Each family converts to whatever actually reads well, rather than forcing
# everything through one format:
#
#   text documents -> HTML  reflows to the screen width, text stays selectable
#   presentations  -> SVG   vector, keeps the layout, and LibreOffice embeds a
#                           navigation script so the slides can be paged
#   spreadsheets   -> HTML  tables reflow
#
# Presentations must NOT go to HTML: LibreOffice's default path for .pptx drops
# every piece of text (documented as bug 148253), and even the corrected
# `html:impress_html_Export` filter emits a bare text dump with no layout.
PREVIEW_TARGET = {
    "doc": "html", "docx": "html", "odt": "html", "rtf": "html",
    "ppt": "svg", "pptx": "svg", "odp": "svg",
    "xls": "html", "xlsx": "html", "ods": "html",
}
_PREVIEW_MEDIA = {"html": "text/html; charset=utf-8", "svg": "image/svg+xml"}
_PREVIEW_CACHE = pathlib.Path(tempfile.gettempdir()) / "oopsbox-preview"
_CONVERT_TIMEOUT = 120


def _preview_file(src: Path, target: str) -> Path:
    """Convert a document, reusing an earlier conversion when possible.

    The cache key covers mtime and size, so editing a file invalidates its
    preview without having to watch the filesystem.
    """
    st = src.stat()
    key = hashlib.sha256(
        f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}|{target}".encode()
    ).hexdigest()[:32]
    cached = _PREVIEW_CACHE / f"{key}.{target}"
    if cached.is_file():
        return cached

    _PREVIEW_CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oopsbox-conv-") as tmp:
        work = Path(tmp)
        try:
            proc = subprocess.run(
                [
                    "soffice", "--headless", "--norestore", "--invisible",
                    # A private profile per run. LibreOffice serialises on a
                    # shared one, so two previews at once would otherwise
                    # block each other or fail outright.
                    f"-env:UserInstallation=file://{work / 'profile'}",
                    "--convert-to", target, "--outdir", str(work), str(src),
                ],
                capture_output=True, text=True, timeout=_CONVERT_TIMEOUT,
            )
        except FileNotFoundError:
            raise HTTPException(
                status_code=501,
                detail="LibreOffice is not installed on the server",
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="Conversion timed out")

        produced = work / f"{src.stem}.{target}"
        if not produced.is_file():
            detail = (proc.stderr or proc.stdout).strip()[:200] or "no output produced"
            raise HTTPException(status_code=500, detail=f"Conversion failed: {detail}")
        shutil.move(str(produced), cached)
    return cached


@router.get("/{project}/preview")
def preview_document(project: str, path: str):
    target_file = _resolve(project, path)
    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    ext = target_file.suffix.lstrip(".").lower()
    target = PREVIEW_TARGET.get(ext)
    if not target:
        raise HTTPException(status_code=400, detail=f"Cannot preview .{ext} as a document")
    converted = _preview_file(target_file, target)
    return FileResponse(
        path=converted,
        filename=f"{target_file.stem}.{target}",
        media_type=_PREVIEW_MEDIA[target],
        content_disposition_type="inline",
    )


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
    # the browser's file name, never a path: "../x" must not leave the folder
    name = uploads.safe_name(file.filename)
    (dest_dir / name).unlink(missing_ok=True)  # the Files tab replaces a file of the same name
    saved, _ = await uploads.save_to_dir(file, dest_dir, name)
    return {"ok": True, "name": saved.name}
