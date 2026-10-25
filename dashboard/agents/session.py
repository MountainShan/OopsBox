# dashboard/agents/session.py
"""One chat between the user and one agent on one project.

The browser never sees an agent's own protocol. Every driver translates what
its agent does into *blocks* — a user message, an assistant reply, a thought,
a tool call, a permission request, a notice — and the session fans block
changes out to every open browser tab and appends them to disk, so the
conversation survives a page reload and a dashboard restart.

Events sent to subscribers:
  {"type": "reset", "blocks": [...], "state": {...}}  first, on subscribe
  {"type": "block", "block": {...}}                   a block was added/changed
  {"type": "delta", "id", "field", "delta"}           text streamed into a block
  {"type": "state", "state": {...}}                   status/autopilot changed

Slash commands are not all alike. Each one the agent advertises carries a
kind that says who handles it:
  prompt    the model does — sent as the text of a message
  action    the agent runs it and answers with a message
  selection like action, but its value is picked from a list first
  panel     the agent answers with data the browser draws as a panel
  local     OopsBox itself handles it
  terminal  only the agent's own TUI can do it — handed to the Terminal tab
Everything but `prompt` becomes a `command` block in the conversation.
"""
import asyncio
import base64
import json
import logging
import shlex
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("oopsbox.chat")

# How much history a page load receives. Older blocks stay on disk.
SNAPSHOT_BLOCKS = 400
# Tool output beyond this is cut; the full text lives in the agent's own log.
MAX_FIELD_CHARS = 20_000

# Statuses a block can be left in only while its agent is alive. A block
# found in one of these after a restart was cut off mid-flight.
_LIVE_STATUSES = {"running", "pending"}


def clip(text: Optional[str], limit: int = MAX_FIELD_CHARS) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… ({len(text) - limit} more characters)"


@dataclass
class ProjectInfo:
    name: str
    path: Path
    type: str = "local"
    ssh_host: Optional[str] = None
    ssh_port: int = 22
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_key_path: Optional[str] = None
    remote_path: Optional[str] = None

    @property
    def is_ssh(self) -> bool:
        return self.type == "ssh"

    @classmethod
    def from_registry(cls, meta: dict) -> "ProjectInfo":
        return cls(
            name=meta["name"],
            path=Path(meta["path"]),
            type=meta.get("type", "local"),
            ssh_host=meta.get("ssh_host"),
            ssh_port=int(meta.get("ssh_port") or 22),
            ssh_user=meta.get("ssh_user"),
            ssh_password=meta.get("ssh_password"),
            ssh_key_path=meta.get("ssh_key_path"),
            remote_path=meta.get("remote_path"),
        )


class ChatStore:
    """Append-only block log plus a small state file, per project and agent.

    Each line of blocks.jsonl is a full block; a later line with the same id
    replaces an earlier one. That keeps writes to a single append and lets a
    crash lose at most the line being written.
    """

    def __init__(self, root: Path):
        self.root = root

    @property
    def _blocks_file(self) -> Path:
        return self.root / "blocks.jsonl"

    @property
    def _state_file(self) -> Path:
        return self.root / "state.json"

    def load_blocks(self) -> list[dict]:
        if not self._blocks_file.exists():
            return []
        by_id: dict[str, dict] = {}
        with open(self._blocks_file, encoding="utf-8") as f:
            for line in f:
                try:
                    block = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(block, dict) and "id" in block:
                    by_id[block["id"]] = block
        by_id = {k: b for k, b in by_id.items() if b.get("kind")}
        for block in by_id.values():
            if block.get("status") in _LIVE_STATUSES:
                block["status"] = "interrupted"
        # Blocks reach the file when they finish, not when they appear (a
        # streamed reply is written once complete), so file order is not
        # conversation order. Every block carries its creation time.
        return sorted(by_id.values(), key=lambda b: b.get("ts", 0))

    def append_block(self, block: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self._blocks_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(block, ensure_ascii=False) + "\n")

    def load_state(self) -> dict:
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_state(self, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)

    def archive(self) -> None:
        """Set the current conversation aside and start an empty one.

        The old block log is kept next to the new one, renamed with a
        timestamp, so starting over never destroys a conversation.
        """
        if self._blocks_file.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self._blocks_file.rename(self.root / f"blocks-{stamp}.jsonl")

    @property
    def uploads(self) -> Path:
        return self.root / "uploads"

    def save_upload(self, data: bytes, mime: str) -> Path:
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp"}.get(mime, ".bin")
        self.uploads.mkdir(parents=True, exist_ok=True)
        path = self.uploads / (uuid.uuid4().hex[:16] + ext)
        path.write_bytes(data)
        return path


@dataclass
class PermissionOption:
    id: str
    label: str
    # "allow_once" | "allow_always" | "reject" — drives the button colour and
    # which option autopilot picks
    kind: str


@dataclass
class _PendingPermission:
    future: asyncio.Future
    options: list[PermissionOption] = field(default_factory=list)


@dataclass
class Attachment:
    """An image the user attached to a message."""
    path: Path      # stored copy on the OopsBox host
    mime: str
    data: bytes

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode()

    @property
    def data_url(self) -> str:
        return f"data:{self.mime};base64,{self.b64}"


def files_note(files: list[dict]) -> str:
    """What the agent reads about files attached to a message."""
    lines = ["[Files the user attached, saved in the project folder]"]
    for f in files:
        size = f.get("size") or 0
        human = f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, size // 1024)} KB"
        lines.append(f"- {f['path']} ({human})")
    return "\n".join(lines)


COMMAND_KINDS = ("prompt", "action", "selection", "panel", "local", "terminal")

# A command's answer is kept in its block; past this it is replaced by a note.
MAX_COMMAND_DATA = 300_000


def command(name: str, description: str = "", kind: str = "prompt", hint: str = "",
            subcommands: Optional[list[dict]] = None) -> dict:
    """A slash command as the browser sees it."""
    assert kind in COMMAND_KINDS, kind
    return {"name": "/" + name.lstrip("/"), "description": description, "kind": kind,
            "hint": hint, "subcommands": subcommands or []}


class CommandUnsupported(Exception):
    """The running agent cannot do this command over its protocol (an older
    or newer CLI than the driver was written for). It falls back to the
    Terminal tab instead of failing outright."""


# Exit codes that mean "someone stopped it" rather than "it crashed":
# SIGTERM/SIGKILL as a negative returncode or as a shell-style 128+n.
_STOPPED_CODES = {0, None, -15, -9, 143, 137}


class AgentSession:
    """Base class. A driver subclass implements the _agent_* hooks.

    Besides chat, a driver can advertise what its agent offers — models,
    reasoning effort, modes, slash commands, image input, context usage —
    by filling the attributes below; the browser builds its controls from
    them.
    """

    agent: str = ""
    # True if a message sent while a turn runs is delivered into that turn;
    # otherwise it waits and is sent when the turn ends.
    can_steer: bool = False
    # Command-line flags OopsBox sets itself to talk to the agent; extra
    # arguments may not override them.
    reserved_flags: frozenset[str] = frozenset()
    # Flags worth offering on the launch form: {flag, arg, description}
    known_flags: list[dict] = []

    def __init__(self, project: ProjectInfo, store: ChatStore, instructions: str = ""):
        self.project = project
        self.store = store
        self.instructions = instructions
        self.status = "stopped"  # stopped | starting | idle | working | error
        self.error: Optional[str] = None
        self.blocks: dict[str, dict] = {b["id"]: b for b in store.load_blocks()}
        self.saved = store.load_state()
        self.autopilot: bool = bool(self.saved.get("autopilot", False))
        # what the agent offers; filled by the driver once it has started
        self.models: list[dict] = []    # {id, name, description, efforts: [..]}
        self.model: Optional[str] = self.saved.get("model")
        self.effort: Optional[str] = self.saved.get("effort")
        self.modes: list[dict] = []     # {id, name, description}
        self.mode: Optional[str] = self.saved.get("mode")
        self.commands: list[dict] = []  # {name, description, hint}
        self.usage: Optional[dict] = None  # {percent, label}
        self.supports_images = False
        self._subscribers: set[asyncio.Queue] = set()
        self._permissions: dict[str, _PendingPermission] = {}
        self._questions: dict[str, asyncio.Future] = {}
        self._queue: list[tuple[str, str, list[Attachment]]] = []
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # ── State shared with the browser ─────────────────
    def state(self) -> dict:
        return {
            "agent": self.agent,
            "status": self.status,
            "error": self.error,
            "autopilot": self.autopilot,
            "models": self.models,
            "model": self.model,
            "effort": self.effort,
            "modes": self.modes,
            "mode": self.mode,
            "commands": self.commands,
            "usage": self.usage,
            "images": self.supports_images,
            "queued": len(self._queue),
            "extra_args": shlex.join(self.extra_args),
            # continued in the Terminal tab: opening the chat must not start
            # a second client on the same conversation
            "handed_off": bool(self.saved.get("handed_off")),
            "known_flags": self.known_flags,
            "features": {
                "conversations": self.lists_conversations,
                "handoff": self.terminal_command() is not None,
            },
        }

    def publish_state(self) -> None:
        self._publish({"type": "state", "state": self.state()})

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        blocks = list(self.blocks.values())[-SNAPSHOT_BLOCKS:]
        q.put_nowait({"type": "reset", "blocks": blocks, "state": self.state()})
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def is_subscribed(self, q: asyncio.Queue) -> bool:
        return q in self._subscribers

    def _publish(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A tab that stopped reading is dropped rather than allowed to
                # grow without bound; it re-syncs from a reset on reconnect.
                self._subscribers.discard(q)

    def set_status(self, status: str, error: Optional[str] = None) -> None:
        was = self.status
        self.status = status
        self.error = error
        self.publish_state()
        if status == "idle" and was == "working" and self._queue:
            asyncio.get_running_loop().create_task(self._send_next_queued())

    def set_usage(self, percent: Optional[float], label: str = "") -> None:
        if percent is None:
            return
        self.usage = {"percent": round(max(0.0, min(100.0, percent)), 1), "label": label}
        self.publish_state()

    def save_state(self, **changes) -> None:
        self.saved.update(changes)
        self.store.save_state(self.saved)

    # ── Blocks ────────────────────────────────────────
    def upsert(self, block: dict, persist: bool = True) -> dict:
        """Add or replace a block. Fields not given keep their old value."""
        existing = self.blocks.get(block["id"])
        if existing is None and "kind" not in block:
            # an update for a block that was never created (or was cleared
            # by a new conversation) has nothing to update
            return block
        merged = {**existing, **block} if existing else {"ts": time.time(), **block}
        self.blocks[merged["id"]] = merged
        self._publish({"type": "block", "block": merged})
        if persist:
            self.store.append_block(merged)
        return merged

    def append_text(self, block_id: str, field_name: str, delta: str, kind: str = "assistant") -> None:
        """Stream text into a block, creating it on the first delta.

        Deltas are not persisted one by one — the driver upserts the finished
        block, which is what lands on disk.
        """
        if not delta:
            return
        block = self.blocks.get(block_id)
        if block is None:
            self.upsert({"id": block_id, "kind": kind, field_name: delta}, persist=False)
            return
        block[field_name] = (block.get(field_name) or "") + delta
        self._publish({"type": "delta", "id": block_id, "field": field_name, "delta": delta})

    def notice(self, text: str, level: str = "info") -> None:
        self.upsert({"id": "n-" + uuid.uuid4().hex[:12], "kind": "notice", "level": level, "text": text})

    # ── Permissions ───────────────────────────────────
    async def ask_permission(self, block_id: str, title: str, detail: str,
                             options: list[PermissionOption], markdown: bool = False) -> str:
        """Show a permission block and wait for the user's choice.

        Returns the chosen option id, or "" if the request was cancelled
        (agent stopped, turn interrupted).
        """
        block = {"id": block_id, "kind": "permission", "title": title, "detail": clip(detail),
                 "options": [o.__dict__ for o in options], "markdown": markdown}
        if self.autopilot:
            pick = next((o for o in options if o.kind == "allow_once"), None) \
                or next((o for o in options if o.kind == "allow_always"), None)
            if pick:
                self.upsert({**block, "status": "resolved", "choice": pick.id, "auto": True})
                return pick.id
        fut = asyncio.get_running_loop().create_future()
        self._permissions[block_id] = _PendingPermission(fut, options)
        self.upsert({**block, "status": "pending"})
        self._attention("permission", title)
        try:
            return await fut
        finally:
            self._permissions.pop(block_id, None)

    def resolve_permission(self, block_id: str, option_id: str) -> bool:
        pending = self._permissions.get(block_id)
        if pending is None or pending.future.done():
            return False
        if option_id not in {o.id for o in pending.options}:
            return False
        self.upsert({"id": block_id, "status": "resolved", "choice": option_id})
        pending.future.set_result(option_id)
        return True

    # ── Questions (the agent asks the user to choose) ─
    async def ask_question(self, block_id: str, questions: list[dict]) -> Optional[dict]:
        """Show multiple-choice questions and wait for answers.

        questions: [{id, header, question, options: [{label, description}], multi}]
        Returns {question id: [chosen labels or typed text]}, or None if
        cancelled. Autopilot does not answer questions — they are the user's.
        """
        fut = asyncio.get_running_loop().create_future()
        self._questions[block_id] = fut
        self.upsert({"id": block_id, "kind": "question", "questions": questions, "status": "pending"})
        first = questions[0] if questions else {}
        self._attention("question", first.get("question") or first.get("header") or "")
        try:
            return await fut
        finally:
            self._questions.pop(block_id, None)

    def answer_question(self, block_id: str, answers: dict) -> bool:
        fut = self._questions.get(block_id)
        if fut is None or fut.done():
            return False
        clean = {str(k): [str(x) for x in (v if isinstance(v, list) else [v]) if str(x).strip()]
                 for k, v in answers.items()}
        self.upsert({"id": block_id, "status": "answered", "answers": clean})
        fut.set_result(clean)
        return True

    def _cancel_waiting(self) -> None:
        for block_id, pending in list(self._permissions.items()):
            if not pending.future.done():
                self.upsert({"id": block_id, "status": "cancelled"})
                pending.future.set_result("")
        for block_id, fut in list(self._questions.items()):
            if not fut.done():
                self.upsert({"id": block_id, "status": "cancelled"})
                fut.set_result(None)

    def set_autopilot(self, enabled: bool) -> None:
        self.autopilot = enabled
        self.save_state(autopilot=enabled)
        self.publish_state()
        if enabled:
            # Whatever is already waiting gets the same answer autopilot would
            # have given, so turning it on unblocks the agent straight away.
            for block_id, pending in list(self._permissions.items()):
                pick = next((o for o in pending.options if o.kind == "allow_once"), None) \
                    or next((o for o in pending.options if o.kind == "allow_always"), None)
                if pick:
                    self.resolve_permission(block_id, pick.id)

    # ── Model / effort / mode ─────────────────────────
    async def set_model(self, model: Optional[str], effort: Optional[str]) -> None:
        if model is not None and self.models and model not in {m["id"] for m in self.models}:
            raise ValueError(f"unknown model {model}")
        if model is not None:
            self.model = model
        if effort is not None:
            self.effort = effort or None
        self.save_state(model=self.model, effort=self.effort)
        if self.alive:
            await self._agent_set_model(self.model, self.effort)
        self.publish_state()

    async def set_mode(self, mode: str) -> None:
        if self.modes and mode not in {m["id"] for m in self.modes}:
            raise ValueError(f"unknown mode {mode}")
        self.mode = mode
        self.save_state(mode=mode)
        if self.alive:
            await self._agent_set_mode(mode)
        self.publish_state()

    # ── Lifecycle (public) ────────────────────────────
    # Set by the manager: called with (kind, text) when the agent stops to
    # wait for the user, so they can be told even with the page closed.
    on_attention: Optional[Callable[[str, str], None]] = None

    def _attention(self, kind: str, text: str) -> None:
        if self.on_attention is not None:
            try:
                self.on_attention(kind, text)
            except Exception:
                log.exception("attention hook failed")

    @property
    def waiting(self) -> bool:
        """True while a permission or a question waits for the user."""
        return any(b.get("kind") in ("permission", "question") and b.get("status") == "pending"
                   for b in self.blocks.values())

    @property
    def alive(self) -> bool:
        return self.status in ("starting", "idle", "working")

    async def start(self) -> None:
        async with self._lock:
            if self.alive:
                return
            self.set_status("starting")
            try:
                await self._agent_start()
            except Exception as e:
                log.exception("%s failed to start for %s", self.agent, self.project.name)
                await self._agent_stop()
                self.set_status("error", str(e) or e.__class__.__name__)
                self.notice(f"{self.agent} could not start: {e}", "error")
                return
            # standby: start it again when OopsBox restarts (manager.restore)
            self.save_state(handed_off=False, standby=True)
            self.set_status("idle")

    async def stop(self, keep_standby: bool = False) -> None:
        """Stop the agent. keep_standby is for OopsBox shutting down: the
        agent was not stopped by anyone, so it comes back on restart."""
        async with self._lock:
            self._cancel_waiting()
            self._queue.clear()
            await self._agent_stop()
            if self.status != "error":
                self.set_status("stopped")
            if not keep_standby and self.saved.get("standby"):
                self.save_state(standby=False)

    async def usage_data(self) -> dict:
        """The account usage the agent's own /usage reports."""
        return (await self._agent_command("/usage", "")).get("data") or {}

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def prompt(self, text: str, attachments: Optional[list[Attachment]] = None,
                     files: Optional[list[dict]] = None) -> None:
        """Send a message. files are uploads saved in the project
        ({path, name, size, mime}); the agent is told where they are, and
        images among them may also arrive as attachments to look at."""
        attachments = attachments or []
        files = files or []
        spec, value = self.parse_command(text)
        if spec and spec["kind"] != "prompt" and not attachments and not files:
            if not self.alive and spec["kind"] != "local":
                await self.start()
            # A command can take a while (/compact); its block shows progress.
            self._spawn(self.run_command(spec["name"], value))
            return
        if not self.alive:
            await self.start()
            if not self.alive:
                raise RuntimeError(self.error or f"{self.agent} is not running")
        block_id = "u-" + uuid.uuid4().hex[:12]
        block = {"id": block_id, "kind": "user", "text": text}
        if files:
            block["files"] = files
            text = (text + "\n\n" if text else "") + files_note(files)
        else:
            block["images"] = [a.path.name for a in attachments]
        if self.status == "working":
            if self.can_steer:
                self.upsert(block)
                await self._agent_steer(text, attachments)
            else:
                self.upsert({**block, "queued": True})
                self._queue.append((block_id, text, attachments))
                self.publish_state()
            return
        if self.status == "starting":
            raise RuntimeError(f"{self.agent} is still starting")
        self.upsert(block)
        await self._send(text, attachments)

    async def _send(self, text: str, attachments: list[Attachment]) -> None:
        self.set_status("working")
        try:
            await self._agent_prompt(text, attachments)
        except Exception as e:
            log.exception("%s prompt failed", self.agent)
            self.notice(f"Could not send: {e}", "error")
            if self.status == "working":
                self.set_status("idle")

    async def _send_next_queued(self) -> None:
        if not self._queue or self.status != "idle":
            return
        block_id, text, attachments = self._queue.pop(0)
        self.upsert({"id": block_id, "queued": False})
        await self._send(text, attachments)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ── Slash commands ────────────────────────────────
    def command_spec(self, name: str) -> Optional[dict]:
        name = "/" + name.lstrip("/")
        for c in self.commands:
            if c["name"] == name:
                return {"kind": "prompt", "hint": "", "subcommands": [], "description": "", **c}
        return None

    def parse_command(self, text: str) -> tuple[Optional[dict], str]:
        """Split "/name rest" into the command's spec and its value."""
        if not text.startswith("/"):
            return None, ""
        name, _, value = text.partition(" ")
        return self.command_spec(name), value.strip()

    async def run_command(self, name: str, value: str = "", block_id: Optional[str] = None) -> dict:
        """Run a non-prompt command and record it as a block.

        block_id reuses an existing command block — a selection the user
        just picked from replaces the list it was picked from.
        """
        spec = self.command_spec(name) or command(name, kind="action")
        bid = block_id or "c-" + uuid.uuid4().hex[:12]
        # every update carries the whole identity: a command that starts a
        # new conversation (/new) clears the block it was shown in
        head = {"id": bid, "kind": "command", "command": spec["name"], "value": value}
        self.upsert({**head, "status": "running", "message": "", "data": None, "options": None}, persist=False)
        result: dict
        try:
            if spec["kind"] == "terminal":
                raise CommandUnsupported()
            if spec["kind"] == "selection" and not value:
                options = await self._agent_command_options(spec["name"], "")
                if options:
                    return self.upsert({**head, "status": "choose", "options": options})
            if spec["kind"] == "local":
                result = await self._local_command(spec["name"], value)
            else:
                result = await self._agent_command(spec["name"], value)
        except (CommandUnsupported, NotImplementedError):
            self._mark_terminal_only(spec["name"])
            result = {"ok": False, "handoff": True,
                      "message": f"{spec['name']} works only in {self.agent}'s own interface: use Open in Terminal."}
        except Exception as e:
            log.exception("%s command %s failed", self.agent, spec["name"])
            result = {"ok": False, "message": str(e) or e.__class__.__name__}
        data = result.get("data")
        if data is not None and len(json.dumps(data, ensure_ascii=False, default=str)) > MAX_COMMAND_DATA:
            data = None
            result["message"] = (result.get("message") or "") + " (result too large to keep)"
        return self.upsert({**head, "status": "ok" if result.get("ok", True) else "error",
                            "message": result.get("message", ""), "data": data,
                            "handoff": bool(result.get("handoff")), "options": None})

    async def command_options(self, name: str, partial: str = "") -> list[dict]:
        spec = self.command_spec(name)
        if spec is None or spec["kind"] != "selection":
            return []
        try:
            return await self._agent_command_options(spec["name"], partial)
        except (CommandUnsupported, NotImplementedError):
            return []

    def _mark_terminal_only(self, name: str) -> None:
        for c in self.commands:
            if c["name"] == name and c.get("kind") != "terminal":
                c["kind"] = "terminal"
                self.publish_state()

    async def _local_command(self, name: str, value: str) -> dict:
        if name == "/new":
            await self.new_conversation()
            return {"message": "Started a new conversation."}
        raise CommandUnsupported()

    # ── Launch arguments ──────────────────────────────
    @property
    def extra_args(self) -> list[str]:
        return list(self.saved.get("extra_args") or [])

    def parse_extra_args(self, text: str) -> list[str]:
        try:
            args = shlex.split(text or "")
        except ValueError as e:
            raise ValueError(f"Could not parse the arguments ({e})")
        for a in args:
            flag = a.split("=", 1)[0]
            if flag in self.reserved_flags:
                raise ValueError(f"{flag} is set by OopsBox and cannot be overridden")
        return args

    async def set_extra_args(self, text: str) -> None:
        args = self.parse_extra_args(text)
        self.save_state(extra_args=args)
        if self.alive:
            await self.restart()
        self.publish_state()

    # ── Other conversations, and the Terminal tab ─────
    lists_conversations = False

    async def list_conversations(self) -> list[dict]:
        """[{id, title, updated (epoch seconds), current}], newest first."""
        return []

    async def resume_conversation(self, conversation_id: str) -> None:
        """Switch this chat to another of the agent's own conversations."""
        await self.stop()
        self.store.archive()
        self.blocks.clear()
        self._set_conversation_id(conversation_id)
        self.store.save_state(self.saved)
        self.usage = None
        self._publish({"type": "reset", "blocks": [], "state": self.state()})
        self.notice("Switched to another conversation." if self.replays_history
                    else "Switched to another conversation. Its earlier messages stay with the agent; only new ones show here.")
        await self.start()

    # True if the agent sends a resumed conversation's history again, so the
    # chat can show it.
    replays_history = False

    def _set_conversation_id(self, conversation_id: str) -> None:
        raise NotImplementedError

    def terminal_command(self) -> Optional[list[str]]:
        """The agent's own CLI, continuing this conversation."""
        return None

    async def cancel(self) -> None:
        self._cancel_waiting()
        if self._queue:
            for block_id, _, _ in self._queue:
                self.upsert({"id": block_id, "queued": False, "dropped": True})
            self._queue.clear()
            self.publish_state()
        if self.status == "working":
            await self._agent_cancel()

    async def new_conversation(self) -> None:
        """Archive this conversation and start a fresh agent session."""
        await self.stop()
        self.store.archive()
        self.blocks.clear()
        for key in ("thread_id", "session_id"):
            self.saved.pop(key, None)
        self.store.save_state(self.saved)
        self.usage = None
        self._publish({"type": "reset", "blocks": [], "state": self.state()})
        await self.start()

    # Called by drivers when their process dies on its own.
    def agent_exited(self, code: Optional[int], stderr_tail: str) -> None:
        self._cancel_waiting()
        self._queue.clear()
        if code in _STOPPED_CODES:
            self.notice(f"{self.agent} stopped.")
            self.set_status("stopped")
            return
        detail = stderr_tail.strip().splitlines()[-5:]
        msg = f"{self.agent} exited unexpectedly (exit {code})"
        if detail:
            msg += "\n" + "\n".join(detail)
        self.notice(msg, "error")
        self.set_status("error", msg)

    # ── Driver hooks ──────────────────────────────────
    async def _agent_start(self) -> None:
        raise NotImplementedError

    async def _agent_stop(self) -> None:
        raise NotImplementedError

    async def _agent_prompt(self, text: str, attachments: list[Attachment]) -> None:
        """Send one user message. Returns once the agent has accepted it;
        the turn's end is reported by setting status back to idle."""
        raise NotImplementedError

    async def _agent_steer(self, text: str, attachments: list[Attachment]) -> None:
        raise NotImplementedError

    async def _agent_cancel(self) -> None:
        raise NotImplementedError

    async def _agent_set_model(self, model: Optional[str], effort: Optional[str]) -> None:
        """Apply a model/effort change to a running agent. Default: the
        driver reads self.model/self.effort on the next turn."""

    async def _agent_set_mode(self, mode: str) -> None:
        """Apply a mode change to a running agent."""

    async def _agent_command(self, name: str, value: str) -> dict:
        """Run an action/selection/panel command.
        Returns {ok, message, data}; raises CommandUnsupported if the agent
        cannot do it over its protocol."""
        raise NotImplementedError

    async def _agent_command_options(self, name: str, partial: str) -> list[dict]:
        """Choices for a selection command: [{value, label, description, group, active}]"""
        raise NotImplementedError
