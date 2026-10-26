# dashboard/routers/chat.py
"""Chat with a project's agents: block stream (SSE) plus control endpoints."""
import asyncio
import base64
import binascii
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Optional, Union

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import uploads
from ..agents import catalog, config_files
from ..agents.manager import DRIVERS, manager
from ..agents.session import Attachment
from .projects import _load_registry, agent_access_of, start_terminal

router = APIRouter(prefix="/api/chat", tags=["chat"])

# nginx drops a proxied response that is silent for 60s; ping well inside that.
_HEARTBEAT_SECONDS = 15


def _meta(name: str) -> dict:
    registry = _load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail="Project not found")
    return registry[name]


def _access(meta: dict) -> dict[str, str]:
    return agent_access_of(meta)


def _session(name: str, agent: str):
    meta = _meta(name)
    if agent not in DRIVERS:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{agent}'")
    if _access(meta).get(agent) == "blocked":
        raise HTTPException(status_code=403, detail=f"{agent} is not allowed on this project")
    return manager.get(meta, agent)


@router.get("/{name}")
def chat_overview(name: str):
    meta = _meta(name)
    access = _access(meta)
    return {
        "type": meta.get("type", "local"),
        "upload_max_mb": uploads.limits()["max_mb"],
        "default": manager.default_agent(name, access),
        "agents": [
            {"name": a, "access": access.get(a, "blocked"), "status": manager.status(name, a),
             "experimental": catalog.SPECS[a].stability == "experimental"}
            for a in access if access[a] != "blocked"
        ],
    }


@router.get("/{name}/{agent}/stream")
async def chat_stream(name: str, agent: str, request: Request):
    session = _session(name, agent)
    manager.set_last_agent(name, agent)
    queue = session.subscribe()

    async def events():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), _HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # A dropped (too slow) subscriber gets no more events;
                    # ending the stream makes the browser reconnect and reset.
                    if await request.is_disconnected() or not session.is_subscribed(queue):
                        break
                    yield ": ping\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            session.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # stream through nginx instead of buffering
    })


class ImagePayload(BaseModel):
    mime: str
    data: str  # base64


class PromptRequest(BaseModel):
    text: str = ""
    images: list[ImagePayload] = []
    files: list[str] = []  # project-relative paths returned by /attachments


_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_UPLOAD_NAME = re.compile(r"^[0-9a-f]{16}\.(png|jpg|gif|webp)$")


@router.post("/{name}/{agent}/prompt")
async def chat_prompt(name: str, agent: str, req: PromptRequest):
    text = req.text.strip()
    if not text and not req.images and not req.files:
        raise HTTPException(status_code=400, detail="Empty message")
    session = _session(name, agent)
    if text == "/new":
        await session.new_conversation()
        return {"ok": True}
    if req.images and not session.supports_images and session.alive:
        raise HTTPException(status_code=400, detail=f"{agent} does not accept images")
    attachments = []
    for img in req.images:
        if img.mime not in _IMAGE_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported image type {img.mime}")
        try:
            data = base64.b64decode(img.data, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(status_code=400, detail="Image is not valid base64")
        if len(data) > _MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image is larger than 10 MB")
        path = session.store.save_upload(data, img.mime)
        attachments.append(Attachment(path=path, mime=img.mime, data=data))
    files = []
    if req.files:
        files, inline = await asyncio.to_thread(_attached_files, _meta(name), req.files,
                                                session.supports_images or not session.alive)
        attachments += inline
    try:
        await session.prompt(text or ("(see the attached image)" if attachments and not files else ""), attachments, files)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@router.get("/{name}/{agent}/uploads/{filename}")
def chat_upload(name: str, agent: str, filename: str):
    if not _UPLOAD_NAME.match(filename):
        raise HTTPException(status_code=404)
    path = _session(name, agent).store.uploads / filename
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


class ModelRequest(BaseModel):
    model: Optional[str] = None
    effort: Optional[str] = None


@router.post("/{name}/{agent}/model")
async def chat_model(name: str, agent: str, req: ModelRequest):
    session = _session(name, agent)
    try:
        await session.set_model(req.model, req.effort)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not change model: {e}")
    return session.state()


class ModeRequest(BaseModel):
    mode: str


@router.post("/{name}/{agent}/mode")
async def chat_mode(name: str, agent: str, req: ModeRequest):
    session = _session(name, agent)
    try:
        await session.set_mode(req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not change mode: {e}")
    return session.state()


class AnswerRequest(BaseModel):
    block_id: str
    answers: dict[str, Union[list[str], str]]


@router.post("/{name}/{agent}/answer")
def chat_answer(name: str, agent: str, req: AnswerRequest):
    session = _session(name, agent)
    if not session.answer_question(req.block_id, req.answers):
        raise HTTPException(status_code=409, detail="This question is no longer waiting for an answer")
    return {"ok": True}


@router.post("/{name}/{agent}/start")
async def chat_start(name: str, agent: str):
    session = _session(name, agent)
    await session.start()
    if session.status == "error":
        raise HTTPException(status_code=500, detail=session.error or "failed to start")
    return session.state()


@router.post("/{name}/{agent}/stop")
async def chat_stop(name: str, agent: str):
    session = _session(name, agent)
    await session.stop()
    return session.state()


@router.post("/{name}/{agent}/cancel")
async def chat_cancel(name: str, agent: str):
    session = _session(name, agent)
    try:
        await session.cancel()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


class PermissionRequest(BaseModel):
    block_id: str
    option_id: str


@router.post("/{name}/{agent}/permission")
def chat_permission(name: str, agent: str, req: PermissionRequest):
    session = _session(name, agent)
    if not session.resolve_permission(req.block_id, req.option_id):
        raise HTTPException(status_code=409, detail="This request is no longer waiting for an answer")
    return {"ok": True}


class AutopilotRequest(BaseModel):
    enabled: bool


@router.post("/{name}/{agent}/autopilot")
def chat_autopilot(name: str, agent: str, req: AutopilotRequest):
    session = _session(name, agent)
    session.set_autopilot(req.enabled)
    return session.state()


@router.post("/{name}/{agent}/new")
async def chat_new(name: str, agent: str):
    """Set the conversation aside (archived on disk) and start a fresh one."""
    session = _session(name, agent)
    await session.new_conversation()
    return session.state()


class CommandRequest(BaseModel):
    name: str
    value: str = ""
    block_id: Optional[str] = None


@router.post("/{name}/{agent}/command")
async def chat_command(name: str, agent: str, req: CommandRequest):
    """Run a slash command; its progress and answer arrive as a block."""
    session = _session(name, agent)
    if session.command_spec(req.name) is None:
        raise HTTPException(status_code=404, detail=f"Unknown command {req.name}")
    if not session.alive and session.command_spec(req.name)["kind"] != "local":
        await session.start()
    session._spawn(session.run_command(req.name, req.value.strip(), req.block_id))
    return {"ok": True}


@router.get("/{name}/{agent}/commands/{command}/options")
async def chat_command_options(name: str, agent: str, command: str, partial: str = ""):
    session = _session(name, agent)
    if not session.alive:
        raise HTTPException(status_code=409, detail=f"{agent} is not running")
    return {"options": await session.command_options(command, partial)}


class LaunchRequest(BaseModel):
    extra_args: str = ""


@router.get("/{name}/{agent}/launch")
def chat_launch(name: str, agent: str):
    session = _session(name, agent)
    return {"extra_args": shlex.join(session.extra_args), "known_flags": session.known_flags,
            "reserved_flags": sorted(session.reserved_flags)}


@router.put("/{name}/{agent}/launch")
async def chat_set_launch(name: str, agent: str, req: LaunchRequest):
    session = _session(name, agent)
    try:
        await session.set_extra_args(req.extra_args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return session.state()


def _local_project_dir(meta: dict) -> Path:
    if meta.get("type") == "ssh":
        raise HTTPException(status_code=409,
                            detail="A remote project's config files are on the remote host: edit them with Open in Terminal")
    return Path(meta["path"])


@router.get("/{name}/{agent}/config")
def chat_config(name: str, agent: str):
    meta = _meta(name)
    _session(name, agent)
    files = config_files.config_files(agent, _local_project_dir(meta))
    return {"files": [{**f.as_dict(), "content": config_files.read_config(f)} for f in files]}


class ConfigRequest(BaseModel):
    scope: str
    content: str


@router.put("/{name}/{agent}/config")
async def chat_set_config(name: str, agent: str, req: ConfigRequest):
    meta = _meta(name)
    session = _session(name, agent)
    try:
        f = config_files.find(agent, _local_project_dir(meta), req.scope)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown scope {req.scope}")
    try:
        config_files.write_config(f, req.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "restart_suggested": session.alive}


@router.get("/{name}/{agent}/conversations")
async def chat_conversations(name: str, agent: str):
    session = _session(name, agent)
    return {"conversations": await session.list_conversations()}


@router.post("/{name}/{agent}/conversations/{conversation_id}/resume")
async def chat_resume(name: str, agent: str, conversation_id: str):
    session = _session(name, agent)
    known = {c["id"] for c in await session.list_conversations()}
    if conversation_id not in known:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await session.resume_conversation(conversation_id)
    return session.state()


@router.post("/{name}/{agent}/handoff")
async def chat_handoff(name: str, agent: str):
    """Continue this conversation in the agent's own CLI, in the Terminal tab.

    The chat's agent process is stopped first: two clients driving one
    conversation would each overwrite the other's turns.
    """
    meta = _meta(name)
    session = _session(name, agent)
    argv = session.terminal_command()
    if argv is None:
        raise HTTPException(status_code=400, detail=f"{agent} cannot be continued in a terminal")
    await session.stop()
    session.save_state(handed_off=True)
    session.publish_state()
    start_terminal(name)
    # the browser types the command into the Terminal tab once it shows
    session.notice(f"Continued in the Terminal tab ({shlex.join(argv)}). Sending a message here starts the web version again.")
    return {"ok": True, "command": shlex.join(argv)}


_FILE_LIST_CAP = 20_000


def _project_files(root: Path) -> list[str]:
    """Paths in the project, as git sees them when it is a repo."""
    r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                       cwd=root, capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        return r.stdout.splitlines()[:_FILE_LIST_CAP]
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
        rel = os.path.relpath(dirpath, root)
        out += [f if rel == "." else f"{rel}/{f}" for f in filenames]
        if len(out) > _FILE_LIST_CAP:
            break
    return out[:_FILE_LIST_CAP]


@router.get("/{name}/{agent}/files")
def chat_files(name: str, agent: str, q: str = ""):
    """Files matching what follows an @ in the composer."""
    meta = _meta(name)
    _session(name, agent)
    if meta.get("type") == "ssh":
        return {"files": []}
    q = q.lower()
    files = _project_files(Path(meta["path"]))
    # file names that start with the query first, then paths containing it
    hits = [f for f in files if q in f.lower()]
    hits.sort(key=lambda f: (not os.path.basename(f).lower().startswith(q), len(f)))
    return {"files": hits[:30]}



# Images up to this size also go to the agent to look at, besides being
# saved; Claude's API refuses larger ones.
_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
_IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".gif": "image/gif", ".webp": "image/webp"}


def _attach_dir(meta: dict) -> str:
    return uploads.limits()["dir"]


@router.post("/{name}/{agent}/attachments")
async def chat_attach(name: str, agent: str, file: UploadFile = File(...)):
    """Save a file for the next message into the project's upload folder."""
    meta = _meta(name)
    _session(name, agent)
    rel_dir = _attach_dir(meta)
    if meta.get("type") == "ssh":
        from .ssh import _resolve_remote, put_file
        tmp, size = await uploads.spool(file)
        try:
            saved = await asyncio.to_thread(put_file, meta, tmp, _resolve_remote(meta, rel_dir),
                                            uploads.safe_name(file.filename))
        finally:
            os.unlink(tmp)
    else:
        path, size = await uploads.save_to_dir(file, Path(meta["path"]) / rel_dir)
        saved = path.name
    rel = f"{rel_dir}/{saved}"
    return {"path": rel, "name": saved, "size": size,
            "mime": _IMAGE_EXT.get(os.path.splitext(saved)[1].lower()) or file.content_type or ""}


def _attached_files(meta: dict, paths: list[str], want_images: bool) -> tuple[list[dict], list[Attachment]]:
    """Check the paths a message names, and read the small images among them."""
    files, inline = [], []
    is_ssh = meta.get("type") == "ssh"
    root = Path(meta["path"]).resolve()
    for rel in paths[:20]:
        mime = _IMAGE_EXT.get(os.path.splitext(rel)[1].lower(), "")
        if is_ssh:
            from .ssh import _resolve_remote, read_bytes
            _resolve_remote(meta, rel)  # rejects paths outside the project
            size = 0
            data = None
            if mime and want_images:
                try:
                    data = read_bytes(meta, rel)
                    size = len(data)
                except Exception:
                    data = None
            local = root / uploads.safe_name(rel)
        else:
            local = (root / rel).resolve()
            if not str(local).startswith(str(root) + os.sep) or not local.is_file():
                raise HTTPException(status_code=400, detail=f"Attachment not found: {rel}")
            size = local.stat().st_size
            data = local.read_bytes() if mime and want_images and size <= _INLINE_IMAGE_BYTES else None
        files.append({"path": rel, "name": os.path.basename(rel), "size": size, "mime": mime})
        if data is not None and len(data) <= _INLINE_IMAGE_BYTES:
            inline.append(Attachment(path=local, mime=mime, data=data))
    return files, inline
