# dashboard/uploads.py
"""Files people upload, saved into a project under a size limit.

The limit and the folder chat attachments go to are set in Settings
(settings.json: upload_max_mb, upload_dir). Uploads are written to disk
as they arrive, so a large file is never held in memory, and stopped as
soon as they pass the limit. nginx caps request bodies on its own
(client_max_body_size); a limit above that cap needs nginx raised too.
"""
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Callable

from fastapi import HTTPException, UploadFile

DEFAULT_MAX_MB = 100
DEFAULT_DIR = "uploads"
MAX_ALLOWED_MB = 10 * 1024
_CHUNK = 1024 * 1024

_settings_file = Path.home() / ".config/oopsbox/settings.json"


def limits() -> dict:
    try:
        s = json.loads(_settings_file.read_text())
    except (OSError, json.JSONDecodeError):
        s = {}
    return {"max_mb": int(s.get("upload_max_mb") or DEFAULT_MAX_MB),
            "dir": s.get("upload_dir") or DEFAULT_DIR}


def max_bytes() -> int:
    return limits()["max_mb"] * 1024 * 1024


def check_dir(rel: str) -> str:
    """A folder inside the project: relative, no '..'."""
    rel = rel.strip().strip("/")
    parts = PurePosixPath(rel).parts
    if not rel or ".." in parts or rel.startswith("~") or "\\" in rel:
        raise ValueError("Give a path inside the project folder, such as uploads")
    return "/".join(parts)


def safe_name(name: str) -> str:
    """The file's own name, with any path the browser sent dropped."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if name in ("", ".", ".."):
        name = "file"
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:180] + dot + ext[-16:]) if dot else name[:200]
    return name


def unique_name(name: str, exists: Callable[[str], bool]) -> str:
    """name, or name-1.ext, name-2.ext … whichever is free."""
    if not exists(name):
        return name
    stem, dot, ext = name.rpartition(".")
    if not stem:
        stem, dot, ext = name, "", ""
    i = 1
    while exists(f"{stem}-{i}{dot}{ext}"):
        i += 1
    return f"{stem}-{i}{dot}{ext}"


def _too_big(limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=f"File is over the {limit // (1024 * 1024)} MB upload limit (change it in Settings)")


async def save_to_dir(file: UploadFile, dest_dir: Path, name: str | None = None) -> tuple[Path, int]:
    """Stream an upload into dest_dir under a free name. Returns (path, size)."""
    limit = max_bytes()
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = unique_name(safe_name(name or file.filename), lambda n: (dest_dir / n).exists())
    part = dest_dir / f".{final}.part"
    size = 0
    try:
        with open(part, "wb") as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise _too_big(limit)
                out.write(chunk)
        os.replace(part, dest_dir / final)
    finally:
        if part.exists():
            part.unlink()
    return dest_dir / final, size


async def spool(file: UploadFile) -> tuple[str, int]:
    """Stream an upload to a temporary file (for sending on over SFTP).
    The caller deletes it. Returns (path, size)."""
    limit = max_bytes()
    fd, tmp = tempfile.mkstemp(prefix="oopsbox-upload-")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await file.read(_CHUNK):
                size += len(chunk)
                if size > limit:
                    raise _too_big(limit)
                out.write(chunk)
    except BaseException:
        os.unlink(tmp)
        raise
    return tmp, size
