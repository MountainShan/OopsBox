# dashboard/agents/claude.py
"""Claude Code, driven through its stream-json mode.

`claude -p --input-format stream-json --output-format stream-json` keeps one
process per conversation and reads user messages from stdin. With
`--permission-prompt-tool stdio`, each permission check arrives as a
`control_request` of subtype `can_use_tool` and waits for our
`control_response` — the same control protocol the Claude Agent SDK uses.

Slash commands: in this mode Claude Code still runs its own local commands
when they arrive as message text (/context, /cost, /config key=value …), so
every command it advertises is sent as text by default. The ones with a
richer answer over the control protocol (/model, /context, /usage, /mcp …)
become panels, and the TUI-only screens it does not advertise (/permissions,
/hooks, /resume, /rewind, /plugin …) are rebuilt here from settings files,
control requests and `claude` subcommands.
"""
import asyncio
import json
import logging
import re
import shlex
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from .launch import build_command
from .rpc import _LINE_LIMIT, kill_process_group
from .session import AgentSession, Attachment, CommandUnsupported, PermissionOption, clip, command

log = logging.getLogger("oopsbox.claude")

_TOOL_KIND = {
    "Bash": "execute", "BashOutput": "execute", "KillShell": "execute",
    "Read": "read", "NotebookRead": "read",
    "Edit": "edit", "MultiEdit": "edit", "Write": "edit", "NotebookEdit": "edit",
    "Grep": "search", "Glob": "search", "WebSearch": "search", "WebFetch": "search",
}


# --permission-mode choices, in the order Claude Code cycles them
MODES = [
    {"id": "manual", "name": "Ask each time", "description": "Ask before every action"},
    {"id": "acceptEdits", "name": "Accept edits", "description": "Edit files without asking; still ask before running commands"},
    {"id": "auto", "name": "Auto", "description": "Claude decides which actions are safe to run without asking"},
    {"id": "plan", "name": "Plan", "description": "Research and plan only; change nothing"},
    {"id": "bypassPermissions", "name": "Bypass permissions", "description": "Never ask for any action"},
]


# Advertised commands that get a structured answer instead of text
_UPGRADED = {
    "model": "selection", "effort": "selection", "context": "panel", "usage": "panel",
    "mcp": "panel", "agents": "panel", "reload-plugins": "action", "reload-skills": "action",
    "rename": "action", "clear": "local", "new": "local", "exit": "local",
}

# TUI screens Claude Code does not advertise in this mode
_EXTRA = [
    command("permissions", "Permission rules (allow / ask / deny)", "panel"),
    command("hooks", "Configured hooks", "panel"),
    command("status", "Account, model, usage and MCP status", "panel"),
    command("resume", "Resume a previous conversation", "local"),
    command("rewind", "Restore files to before a message", "panel"),
    command("tasks", "Background tasks", "panel"),
    command("plugin", "Plugins", "panel", "[install|uninstall|enable|disable <name>]",
            [{"name": n, "hint": "<plugin>", "description": d} for n, d in (
                ("install", "Install"), ("uninstall", "Uninstall"), ("enable", "Enable"), ("disable", "Disable"))]),
    command("output-style", "Output style", "selection"),
    command("add-dir", "Allow claude to access another folder (restarts it)", "local", "<path>"),
    command("memory", "Edit CLAUDE.md", "local"),
    command("export", "Copy the conversation as Markdown", "local"),
    command("new", "Start a new conversation", "local"),
    command("quit", "Stop claude", "local"),
    *[command(n, d, "terminal") for n, d in (
        ("login", "Log in to Claude"), ("logout", "Log out"), ("doctor", "Check the installation"), ("bug", "Report a bug"),
        ("ide", "Connect to an IDE"), ("vim", "Vim mode"), ("terminal-setup", "Terminal setup"),
        ("statusline", "Status line"), ("theme", "Theme"), ("keybindings", "Keyboard shortcuts"),
        ("privacy-settings", "Privacy settings"), ("install-github-app", "Install the GitHub App"))],
]


class ClaudeSession(AgentSession):
    agent = "claude"
    can_steer = True  # stream-json queues a message sent mid-turn into the turn
    # the stream protocol itself; everything else on `claude --help` is open
    reserved_flags = frozenset({"-p", "--print", "--input-format", "--output-format",
                                "--permission-prompt-tool", "--replay-user-messages",
                                "-h", "--help", "-v", "--version"})
    lists_conversations = True
    replays_history = True
    known_flags = [
        {"flag": "--add-dir", "arg": "<dir>", "description": "Other folders it may access"},
        {"flag": "--allowed-tools", "arg": "<tools>", "description": "Tools allowed without asking, e.g. \"Bash(git *) Edit\""},
        {"flag": "--disallowed-tools", "arg": "<tools>", "description": "Tools it may not use"},
        {"flag": "--tools", "arg": "<tools>", "description": "Offer only these built-in tools"},
        {"flag": "--mcp-config", "arg": "<file|json>", "description": "Extra MCP server config"},
        {"flag": "--strict-mcp-config", "arg": "", "description": "Use only the servers from --mcp-config"},
        {"flag": "--settings", "arg": "<file|json>", "description": "Extra config file"},
        {"flag": "--setting-sources", "arg": "user,project,local", "description": "Which settings sources to read"},
        {"flag": "--agent", "arg": "<name>", "description": "Use a custom agent"},
        {"flag": "--agents", "arg": "<json>", "description": "Define custom agents"},
        {"flag": "--system-prompt", "arg": "<text>", "description": "Replace the default system prompt"},
        {"flag": "--plugin-dir", "arg": "<path>", "description": "Plugins to load for this session only"},
        {"flag": "--fallback-model", "arg": "<model>", "description": "Model to use when the main one is overloaded"},
        {"flag": "--max-budget-usd", "arg": "<amount>", "description": "Spending limit"},
        {"flag": "--autocompact", "arg": "auto|<tokens>", "description": "Auto-compact threshold"},
        {"flag": "--chrome", "arg": "", "description": "Enable Claude in Chrome"},
        {"flag": "--worktree", "arg": "[name]", "description": "Work in a new git worktree"},
        {"flag": "--safe-mode", "arg": "", "description": "Disable all customizations (for troubleshooting)"},
        {"flag": "--restricted", "arg": "", "description": "Restricted mode: remove tools that run commands"},
        {"flag": "--bare", "arg": "", "description": "Minimal mode"},
        {"flag": "--betas", "arg": "<betas>", "description": "API beta headers (API key users)"},
        {"flag": "--debug", "arg": "[filter]", "description": "Debug mode"},
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._stderr: deque[str] = deque(maxlen=40)
        self._control: dict[str, asyncio.Future] = {}
        # streamed content blocks waiting for their finished assistant message,
        # per API message id and content type, in arrival order
        self._streamed: dict[str, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
        self._stream_index: dict[tuple[str, int], str] = {}
        self._message_id: Optional[str] = None
        self._permission_requests: dict[str, str] = {}  # request_id → block id
        self._closing = False
        self._context_window: Optional[int] = None
        self._last_input_tokens: Optional[int] = None
        self._mode_before_plan: Optional[str] = None
        self._running_effort: Optional[str] = None
        self._init: dict = {}
        self._tasks_live: list[dict] = []  # background tasks, from system messages
        self._show_history = False

    # ── Lifecycle ─────────────────────────────────────
    async def _agent_start(self) -> None:
        argv = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose", "--include-partial-messages",
            "--permission-prompt-tool", "stdio",
            # echoes each user message with its uuid, which /rewind needs
            "--replay-user-messages",
        ]
        if self.saved.get("session_id"):
            argv += ["--resume", self.saved["session_id"]]
        if self.model and self.model != "default":
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.mode:
            argv += ["--permission-mode", self.mode]
        if self.instructions:
            argv += ["--append-system-prompt", self.instructions]
        argv += self.extra_args
        argv, cwd, env = build_command(self.project, argv, {
            "CLAUDE_CODE_NO_FLICKER": "1",
            # what the TUI does by default and the SDK must ask for: keep
            # file checkpoints so /rewind can restore them
            "CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING": "1",
        })
        self._closing = False
        self._proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=_LINE_LIMIT, start_new_session=True,
        )
        self._spawn(self._read_stdout())
        self._spawn(self._read_stderr())
        init = await self._control_request({"subtype": "initialize", "hooks": None}, timeout=90)
        self.models = [_model_entry(m) for m in init.get("models") or [] if m.get("value")]
        # "claude-haiku-4-5-20251001", "claude-opus-5[1m]" → "claude-haiku-4-5", "claude-opus-5"
        offered = {re.sub(r"(\[.*\]|-\d{8})$", "", m["resolved"]) for m in self.models} | {m["id"] for m in self.models}
        self.models += [dict(m) for m in OTHER_MODELS if m["id"] not in offered]
        if self.model and self.model not in {m["id"] for m in self.models}:
            self.models.append(_custom_model(self.model))
        self.model = self.model or "default"
        self._init = init
        advertised = {c["name"] for c in init.get("commands") or [] if c.get("name")}
        self.commands = [command(c["name"], (c.get("description") or "")[:200],
                                 _UPGRADED.get(c["name"], "prompt"), c.get("argumentHint") or "")
                         for c in init.get("commands") or [] if c.get("name")]
        self.commands += [c for c in _EXTRA if c["name"][1:] not in advertised]
        self.modes = MODES
        self.mode = self.mode or init.get("current_permission_mode") or "manual"
        self.supports_images = True
        self._running_effort = self.effort
        if self._show_history:
            self._show_history = False
            self._render_history()

    async def _agent_stop(self) -> None:
        self._closing = True
        if self._proc:
            await kill_process_group(self._proc)
        self._proc = None

    def _message(self, text: str, attachments: list[Attachment]) -> dict:
        if not attachments:
            content = text
        else:
            content = [{"type": "image", "source": {"type": "base64", "media_type": a.mime, "data": a.b64}}
                       for a in attachments] + [{"type": "text", "text": text}]
        return {"type": "user", "message": {"role": "user", "content": content}}

    async def _agent_prompt(self, text: str, attachments: list[Attachment]) -> None:
        await self._write(self._message(text, attachments))

    async def _agent_steer(self, text: str, attachments: list[Attachment]) -> None:
        await self._write(self._message(text, attachments))

    async def _agent_set_model(self, model, effort) -> None:
        await self._control_request({"subtype": "set_model", "model": None if model == "default" else model},
                                    timeout=30)
        # Effort has no live control; it applies when the process restarts
        # with --effort, which --resume makes seamless between turns.
        if effort != self._running_effort and self.status == "idle":
            await self.restart()

    async def _agent_set_mode(self, mode: str) -> None:
        await self._control_request({"subtype": "set_permission_mode", "mode": mode}, timeout=30)

    async def _agent_cancel(self) -> None:
        await self._control_request({"subtype": "interrupt"}, timeout=30)

    async def _write(self, msg: dict) -> None:
        if not self._proc or self._proc.returncode is not None:
            raise ConnectionError("claude is not running")
        self._proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
        await self._proc.stdin.drain()

    async def _control_request(self, request: dict, timeout: float) -> dict:
        rid = "req-" + uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._control[rid] = fut
        try:
            await self._write({"type": "control_request", "request_id": rid, "request": request})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._control.pop(rid, None)

    # ── Slash commands ────────────────────────────────
    async def _ctl(self, request: dict, timeout: float = 60) -> dict:
        try:
            return await self._control_request(request, timeout=timeout)
        except RuntimeError as e:
            if "Unsupported" in str(e) or "does not handle" in str(e) or "unknown" in str(e).lower():
                raise CommandUnsupported() from e
            raise

    async def _agent_command_options(self, name: str, partial: str) -> list[dict]:
        if name == "/model":
            return [{"value": m["id"], "label": m["name"],
                     "description": " · ".join(x for x in (m.get("resolved"), m.get("description")) if x),
                     "group": m.get("group", ""), "active": m["id"] == self.model} for m in self.models]
        if name == "/effort":
            model = next((m for m in self.models if m["id"] == self.model), None)
            return [{"value": e, "label": e, "description": "", "group": "", "active": e == self.effort}
                    for e in (model or {}).get("efforts") or []]
        if name == "/output-style":
            current = self._init.get("output_style")
            return [{"value": o, "label": o, "description": "", "group": "", "active": o == current}
                    for o in self._init.get("available_output_styles") or []]
        return []

    async def _agent_command(self, name: str, value: str) -> dict:
        sub, _, rest = value.partition(" ")
        rest = rest.strip()
        if name == "/model":
            await self.set_model(value, None)
            return {"message": f"Model set to {value}."}
        if name == "/effort":
            if not value:
                return {"ok": False, "message": "This model has no adjustable reasoning effort."}
            await self.set_model(None, value)
            return {"message": f"Reasoning effort set to {value}."}
        if name == "/output-style":
            await self._ctl({"subtype": "apply_flag_settings", "settings": {"outputStyle": value}})
            self._init["output_style"] = value
            return {"message": f"Output style set to {value} (for this conversation; to keep it, set outputStyle in ⚙)."}
        if name == "/context":
            return {"data": await self._ctl({"subtype": "get_context_usage"})}
        if name == "/usage":
            return {"data": await self._ctl({"subtype": "get_usage", "skip_behaviors": True})}
        if name == "/status":
            usage = await self._ctl({"subtype": "get_usage", "skip_behaviors": True})
            mcp = await self._ctl({"subtype": "mcp_status"})
            return {"data": {
                "account": self._init.get("account"), "model": self.model, "effort": self.effort,
                "mode": self.mode, "session": self.saved.get("session_id"), "cwd": str(self.project.path),
                "context": self.usage, "rate_limits": usage.get("rate_limits"),
                "mcp": [{"name": m.get("name"), "status": m.get("status")} for m in mcp.get("mcpServers") or []],
            }}
        if name == "/mcp":
            if sub in ("enable", "disable") and rest:
                await self._ctl({"subtype": "mcp_toggle", "serverName": rest, "enabled": sub == "enable"})
                return {"message": f"{rest} {'enabled' if sub == 'enable' else 'disabled'}."}
            if sub == "reconnect" and rest:
                await self._ctl({"subtype": "mcp_reconnect", "serverName": rest}, timeout=120)
                return {"message": f"Reconnected {rest}."}
            res = await self._ctl({"subtype": "mcp_status"})
            return {"data": {"servers": [{"name": m.get("name"), "status": m.get("status"),
                                          "scope": m.get("scope"), "type": (m.get("config") or {}).get("type"),
                                          "error": m.get("error")} for m in res.get("mcpServers") or []]}}
        if name == "/agents":
            return {"data": {"agents": self._init.get("agents") or []}}
        if name in ("/permissions", "/hooks"):
            res = await self._ctl({"subtype": "get_settings"})
            eff = res.get("effective") or {}
            if name == "/hooks":
                return {"data": {"hooks": eff.get("hooks") or {}}}
            perms = eff.get("permissions") or {}
            return {"data": {"mode": self.mode, "allow": perms.get("allow") or [], "ask": perms.get("ask") or [],
                             "deny": perms.get("deny") or [],
                             "additionalDirectories": perms.get("additionalDirectories") or []}}
        if name == "/rewind":
            if value:
                res = await self._ctl({"subtype": "rewind_files", "user_message_id": value}, timeout=120)
                if res.get("error") or not res.get("canRewind", True):
                    return {"ok": False, "message": res.get("error") or "Could not restore."}
                files = res.get("filesChanged")  # not always reported
                done = f"Restored {len(files)} files." if files else "Files restored."
                return {"message": done +
                        " (Changes made with Bash are not in the checkpoint and cannot be restored, same as claude's own /rewind.)"
                        " The conversation itself was not rewound; to rewind it too, use /rewind in the Terminal tab.",
                        "data": {"files": files} if files else None}
            users = [{"id": b["message_uuid"], "text": b["text"][:120]} for b in self.blocks.values()
                     if b["kind"] == "user" and b.get("message_uuid")]
            return {"data": {"messages": users[::-1]}}
        if name == "/tasks":
            if sub == "stop" and rest:
                await self._ctl({"subtype": "stop_task", "task_id": rest})
                return {"message": "Stopped."}
            return {"data": {"tasks": self._tasks_live}}
        if name == "/plugin":
            if sub in ("install", "uninstall", "enable", "disable") and rest:
                out = await self._claude_cli(["plugin", sub, rest])
                try:
                    await self._ctl({"subtype": "reload_plugins"}, timeout=120)
                except Exception:
                    pass
                return {"message": out.strip()[-400:] or "Done."}
            out = await self._claude_cli(["plugin", "list", "--json"])
            try:
                return {"data": {"plugins": json.loads(out)}}
            except json.JSONDecodeError:
                return {"ok": False, "message": out.strip()[-400:]}
        if name in ("/reload-plugins", "/reload-skills"):
            await self._ctl({"subtype": "reload_plugins" if name == "/reload-plugins" else "reload_skills"}, timeout=120)
            return {"message": "Reloaded."}
        if name == "/rename":
            if not value:
                return {"ok": False, "message": "Usage: /rename <name>"}
            await self._ctl({"subtype": "rename_session", "title": value})
            return {"message": f"Conversation renamed to \"{value}\"."}
        raise CommandUnsupported()

    async def _claude_cli(self, args: list[str]) -> str:
        """Run a `claude` subcommand where this project's agent runs."""
        argv, cwd, env = build_command(self.project, ["claude", *args])
        proc = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 300)
        return out.decode(errors="replace")

    async def _local_command(self, name: str, value: str) -> dict:
        if name in ("/new", "/clear"):
            await self.new_conversation()
            return {"message": "Started a new conversation."}
        if name == "/resume":
            return {"data": {"open": "conversations"}}
        if name in ("/exit", "/quit"):
            self._spawn(self.stop())
            return {"message": "claude stopped."}
        if name == "/add-dir":
            if not value:
                return {"ok": False, "message": "Usage: /add-dir <path>"}
            await self.set_extra_args(" ".join(shlex.quote(a) for a in self.extra_args + ["--add-dir", value]))
            return {"message": f"Added {value} and restarted claude (saved in ⚙ launch arguments)."}
        if name == "/memory":
            return {"message": "Edit CLAUDE.md directly in the Files tab; the user-level one is ~/.claude/CLAUDE.md."}
        if name == "/export":
            lines = []
            for b in self.blocks.values():
                if b["kind"] == "user":
                    lines.append("> " + b.get("text", "").replace("\n", "\n> "))
                elif b["kind"] == "assistant":
                    lines.append(b.get("text", ""))
            return {"message": "Copied the conversation as Markdown.", "data": {"copy": "\n\n".join(lines)}}
        return await super()._local_command(name, value)

    # ── Other conversations ───────────────────────────
    def _transcripts(self):
        resolved = str(self.project.path.resolve())
        return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", resolved)

    async def list_conversations(self) -> list[dict]:
        if self.project.is_ssh:
            return []
        return await asyncio.to_thread(self._scan_conversations)

    def _scan_conversations(self) -> list[dict]:
        out = []
        for f in self._transcripts().glob("*.jsonl"):
            title = ""
            try:
                with open(f, encoding="utf-8") as fh:
                    for line in fh:
                        o = json.loads(line)
                        if o.get("type") == "summary" and o.get("summary"):
                            title = o["summary"]
                            break
                        if not title and o.get("type") == "user" and not o.get("isMeta") and not o.get("isSidechain"):
                            title = _user_text(o.get("message") or {})[:100]
            except (OSError, json.JSONDecodeError):
                continue
            if not title:
                continue
            out.append({"id": f.stem, "title": title, "updated": f.stat().st_mtime,
                        "current": f.stem == self.saved.get("session_id")})
        return sorted(out, key=lambda c: c["updated"], reverse=True)[:100]

    def _set_conversation_id(self, conversation_id: str) -> None:
        self.saved["session_id"] = conversation_id
        self._show_history = True

    def _render_history(self) -> None:
        f = self._transcripts() / f"{self.saved.get('session_id')}.jsonl"
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            if o.get("isSidechain") or o.get("isMeta") or o.get("isCompactSummary"):
                continue  # what the TUI does not show either
            msg = o.get("message") or {}
            if o.get("type") == "user":
                text = _user_text(msg)
                if text:
                    self.upsert({"id": "h-" + o.get("uuid", uuid.uuid4().hex), "kind": "user", "text": text,
                                 "message_uuid": o.get("uuid")})
                self._tool_results(msg)
            elif o.get("type") == "assistant":
                self._assistant(msg)

    def terminal_command(self) -> Optional[list[str]]:
        sid = self.saved.get("session_id")
        return ["claude", "--resume", sid] + self.extra_args if sid else ["claude"] + self.extra_args

    # ── Reading ───────────────────────────────────────
    async def _read_stderr(self) -> None:
        while self._proc:
            line = await self._proc.stderr.readline()
            if not line:
                return
            self._stderr.append(line.decode(errors="replace").rstrip())

    async def _read_stdout(self) -> None:
        proc = self._proc
        while True:
            try:
                line = await proc.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                continue
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                await self._handle(msg)
            except Exception:
                log.exception("failed to handle %s", msg.get("type"))
        code = await proc.wait()
        for fut in self._control.values():
            if not fut.done():
                fut.set_exception(ConnectionError("claude exited"))
        if not self._closing and proc is self._proc:
            self._proc = None
            self.agent_exited(code, "\n".join(self._stderr))

    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "control_response":
            resp = msg.get("response") or {}
            fut = self._control.get(resp.get("request_id"))
            if fut and not fut.done():
                if resp.get("subtype") == "error":
                    fut.set_exception(RuntimeError(resp.get("error") or "control request failed"))
                else:
                    fut.set_result(resp.get("response") or {})
        elif t == "control_request":
            self._spawn(self._answer_control(msg))
        elif t == "control_cancel_request":
            block_id = self._permission_requests.pop(msg.get("request_id"), None)
            if block_id:
                pending = self._permissions.get(block_id)
                if pending and not pending.future.done():
                    self.upsert({"id": block_id, "status": "cancelled"})
                    pending.future.set_result("")
                question = self._questions.get(block_id)
                if question and not question.done():
                    self.upsert({"id": block_id, "status": "cancelled"})
                    question.set_result(None)
        elif t == "system":
            if msg.get("subtype") == "init" and msg.get("session_id"):
                if msg["session_id"] != self.saved.get("session_id"):
                    self.save_state(session_id=msg["session_id"])
            elif msg.get("subtype") == "compact_boundary":
                self.notice("Conversation compacted.")
            elif msg.get("subtype") == "background_tasks_changed":
                self._tasks_live = msg.get("tasks") or []
        elif t == "stream_event":
            self._stream_event(msg.get("event") or {})
        elif t == "assistant":
            if msg.get("parent_tool_use_id"):
                return  # a sub-agent's inner turns; its Task tool block covers it
            self._assistant(msg.get("message") or {})
        elif t == "user":
            if msg.get("parent_tool_use_id"):
                return
            text = _user_text(msg.get("message") or {})
            if msg.get("uuid") and text:
                # our own message, echoed by --replay-user-messages; Claude
                # Code also sends messages of its own (a compaction summary),
                # so pair by text
                block = next((b for b in reversed(list(self.blocks.values()))
                              if b["kind"] == "user" and not b.get("message_uuid")
                              and b.get("text", "").strip() == text), None)
                if block:
                    self.upsert({"id": block["id"], "message_uuid": msg["uuid"]})
                return
            self._tool_results(msg.get("message") or {})
        elif t == "result":
            if msg.get("is_error"):
                text = str(msg.get("result") or msg.get("subtype") or "error")
                # an API error usually also arrives as the assistant's reply;
                # only add a notice when it would say something new
                last = next((b for b in reversed(self.blocks.values()) if b["kind"] == "assistant"), None)
                if not last or last.get("text") != text:
                    self.notice(text, "error")
            elif msg.get("subtype") not in (None, "success"):
                self.notice(f"Turn ended: {msg.get('subtype')}", "warn")
            self._streamed.clear()
            self._stream_index.clear()
            for mu in (msg.get("modelUsage") or {}).values():
                if mu.get("contextWindow"):
                    self._context_window = mu["contextWindow"]
            if self._context_window and self._last_input_tokens:
                used = self._last_input_tokens
                self.set_usage(100 * used / self._context_window,
                               f"{used // 1000}k / {self._context_window // 1000}k tokens")
            if self.status == "working":
                self.set_status("idle")

    # Live text: stream_event deltas build the block while it is written.
    def _stream_event(self, ev: dict) -> None:
        et = ev.get("type")
        if et == "message_start":
            self._message_id = (ev.get("message") or {}).get("id")
        elif et == "content_block_start":
            cb = ev.get("content_block") or {}
            kind = {"text": "assistant", "thinking": "thought"}.get(cb.get("type"))
            if not kind or not self._message_id:
                return
            bid = f"{self._message_id}-{ev.get('index')}"
            self._stream_index[(self._message_id, ev.get("index"))] = bid
            self._streamed[self._message_id][cb["type"]].append(bid)
            # the block appears with its first text; thinking is often empty
            # (redacted) and must not leave a blank row behind
        elif et == "content_block_delta":
            bid = self._stream_index.get((self._message_id, ev.get("index")))
            d = ev.get("delta") or {}
            if bid and d.get("type") == "text_delta":
                self.append_text(bid, "text", d.get("text", ""))
            elif bid and d.get("type") == "thinking_delta":
                self.append_text(bid, "text", d.get("thinking", ""), kind="thought")

    # Finished content: the assistant message is authoritative and persisted.
    def _assistant(self, message: dict) -> None:
        mid = message.get("id") or uuid.uuid4().hex
        usage = message.get("usage") or {}
        if usage.get("input_tokens") is not None:
            self._last_input_tokens = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                                       + usage.get("cache_creation_input_tokens", 0))
        for part in message.get("content") or []:
            ptype = part.get("type")
            if ptype in ("text", "thinking"):
                text = part.get("text") if ptype == "text" else part.get("thinking")
                queue = self._streamed.get(mid, {}).get(ptype)
                bid = queue.popleft() if queue else f"{mid}-{uuid.uuid4().hex[:6]}"
                if not text:
                    continue
                self.upsert({"id": bid, "kind": "assistant" if ptype == "text" else "thought", "text": text})
            elif ptype == "tool_use":
                name = part.get("name", "tool")
                args = part.get("input") or {}
                if name == "TodoWrite":
                    marks = {"completed": "☑", "in_progress": "▶", "pending": "☐"}
                    self.upsert({"id": "todo-" + str(self.saved.get("session_id")), "kind": "plan",
                                 "text": "\n".join(f"{marks.get(t.get('status'), '•')} {t.get('content', '')}"
                                                   for t in args.get("todos") or [])})
                    continue
                self.upsert({
                    "id": "tu-" + part.get("id", uuid.uuid4().hex),
                    "kind": "tool",
                    "tool": _TOOL_KIND.get(name, "other"),
                    "title": self._tool_title(name, args),
                    "detail": clip(self._tool_detail(name, args)),
                    "status": "running",
                })

    def _tool_results(self, message: dict) -> None:
        content = message.get("content")
        if not isinstance(content, list):
            return
        for part in content:
            if part.get("type") != "tool_result":
                continue
            bid = "tu-" + str(part.get("tool_use_id"))
            if bid not in self.blocks:
                continue
            out = part.get("content")
            if isinstance(out, list):
                out = "\n".join(c.get("text", "") for c in out if isinstance(c, dict))
            self.upsert({
                "id": bid,
                "status": "failed" if part.get("is_error") else "completed",
                "output": clip(str(out or "")),
            })

    def _rel(self, path: str) -> str:
        root = str(self.project.path) + "/"
        return path[len(root):] if isinstance(path, str) and path.startswith(root) else str(path)

    def _tool_title(self, name: str, args: dict) -> str:
        if name == "Bash":
            return args.get("command", "Bash")
        if name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
            verb = {"Read": "Read", "Write": "Write"}.get(name, "Edit")
            return f"{verb} {self._rel(args.get('file_path') or args.get('notebook_path', ''))}"
        if name in ("Grep", "Glob"):
            return f"{name} {args.get('pattern', '')}"
        if name in ("WebFetch", "WebSearch"):
            return f"{name} {args.get('url') or args.get('query', '')}"
        if name == "Task":
            return "Subagent: " + (args.get("description") or "")
        return name

    def _tool_detail(self, name: str, args: dict) -> str:
        if name == "Bash":
            return args.get("description", "")
        if name == "Edit":
            return f"- {args.get('old_string', '')}\n+ {args.get('new_string', '')}"
        if name == "Write":
            return args.get("content", "")
        return json.dumps(args, ensure_ascii=False, indent=2)

    # ── Permission checks ─────────────────────────────
    async def _answer_control(self, msg: dict) -> None:
        rid = msg.get("request_id")
        req = msg.get("request") or {}
        if req.get("subtype") == "elicitation":
            await self._respond(rid, await self._elicit(req))
            return
        if req.get("subtype") != "can_use_tool":
            await self._write({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid,
                "error": f"OopsBox does not handle {req.get('subtype')}"}})
            return
        name = req.get("tool_name", "tool")
        args = req.get("input") or {}
        suggestions = req.get("permission_suggestions")
        if name == "AskUserQuestion":
            await self._answer_question(rid, req, args)
            return
        if name == "ExitPlanMode":
            await self._answer_plan(rid, req, args)
            return
        block_id = "perm-" + (req.get("tool_use_id") or rid)
        self._permission_requests[rid] = block_id
        options = [PermissionOption("allow", "Allow", "allow_once")]
        if suggestions:
            options.append(PermissionOption("always", "Allow for this conversation", "allow_always"))
        options.append(PermissionOption("deny", "Deny", "reject"))
        detail = self._tool_title(name, args)
        extra = self._tool_detail(name, args)
        if extra and extra != detail:
            detail += "\n\n" + extra
        try:
            choice = await self.ask_permission(block_id, f"Use {name}", detail, options)
        finally:
            self._permission_requests.pop(rid, None)
        if choice in ("allow", "always"):
            response = {"behavior": "allow", "updatedInput": args}
            if choice == "always" and suggestions:
                response["updatedPermissions"] = suggestions
        else:
            response = {"behavior": "deny", "message": "The user denied this action."}
        try:
            await self._write({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": response}})
        except ConnectionError:
            pass

    async def _respond(self, rid: str, response: dict) -> None:
        try:
            await self._write({"type": "control_response", "response": {
                "subtype": "success", "request_id": rid, "response": response}})
        except ConnectionError:
            pass

    async def _elicit(self, req: dict) -> dict:
        """An MCP server asks the user for input: a form, or a page to visit."""
        server = req.get("mcp_server_name", "MCP")
        bid = "q-" + uuid.uuid4().hex[:12]
        if req.get("mode") == "url":
            answers = await self.ask_question(bid, [{
                "id": "done", "header": server, "question": f"{req.get('message', '')}\n{req.get('url', '')}",
                "options": [{"label": "Done", "description": ""}, {"label": "Cancel", "description": ""}],
                "multi": False, "other": False}])
            return {"action": "accept" if (answers or {}).get("done") == ["Done"] else "decline"}
        props = (req.get("requested_schema") or {}).get("properties") or {}
        questions = [{"id": k, "header": server if i == 0 else "",
                      "question": (req.get("message", "") + "\n\n" if i == 0 else "") + (p.get("title") or p.get("description") or k),
                      "options": [{"label": str(o), "description": ""} for o in
                                  (["true", "false"] if p.get("type") == "boolean" else p.get("enum") or [])],
                      "multi": p.get("type") == "array", "other": not (p.get("enum") or p.get("type") == "boolean")}
                     for i, (k, p) in enumerate(props.items())]
        answers = await self.ask_question(bid, questions or [{
            "id": "_ok", "header": server, "question": req.get("message", ""),
            "options": [{"label": "OK", "description": ""}], "multi": False, "other": False}])
        if not answers:
            return {"action": "cancel"}
        content = {}
        for k, p in props.items():
            vals = answers.get(k) or []
            if vals:
                t = p.get("type")
                content[k] = vals if t == "array" else (vals[0] == "true" if t == "boolean" else
                             (float(vals[0]) if t == "number" and vals[0].replace(".", "", 1).isdigit() else
                              (int(vals[0]) if t == "integer" and vals[0].isdigit() else vals[0])))
        return {"action": "accept", "content": content}

    async def _answer_question(self, rid: str, req: dict, args: dict) -> None:
        qs = args.get("questions") or []
        questions = [{
            "id": str(i), "header": q.get("header", ""), "question": q.get("question", ""),
            "options": [{"label": o.get("label", ""), "description": o.get("description", "")}
                        for o in q.get("options") or []],
            "multi": bool(q.get("multiSelect")), "other": True,
        } for i, q in enumerate(qs)]
        block_id = "q-" + (req.get("tool_use_id") or rid)
        self._permission_requests[rid] = block_id
        try:
            answers = await self.ask_question(block_id, questions)
        finally:
            self._permission_requests.pop(rid, None)
        if answers is None:
            await self._respond(rid, {"behavior": "deny", "message": "The user did not answer."})
            return
        by_text = {qs[int(k)].get("question", k): ", ".join(v) for k, v in answers.items() if k.isdigit()}
        await self._respond(rid, {"behavior": "allow", "updatedInput": {**args, "answers": by_text}})

    async def _answer_plan(self, rid: str, req: dict, args: dict) -> None:
        block_id = "perm-" + (req.get("tool_use_id") or rid)
        self._permission_requests[rid] = block_id
        try:
            choice = await self.ask_permission(block_id, "The plan is ready. Start carrying it out?", args.get("plan", ""), [
                PermissionOption("go", "Start", "allow_once"),
                PermissionOption("keep", "Keep planning", "reject"),
            ], markdown=True)
        finally:
            self._permission_requests.pop(rid, None)
        if choice == "go":
            await self._respond(rid, {"behavior": "allow", "updatedInput": args})
            # leaving plan mode: back to whatever mode was used before it
            back = self._mode_before_plan or "manual"
            self.mode = back
            self.save_state(mode=back)
            self.publish_state()
            try:
                await self._agent_set_mode(back)
            except Exception:
                pass
        else:
            await self._respond(rid, {"behavior": "deny", "message": "The user wants to keep planning."})

    async def set_model(self, model: Optional[str], effort: Optional[str]) -> None:
        # like `/model <name>` in the TUI: any model id or alias is allowed,
        # not only the ones the picker lists
        if model and model not in {m["id"] for m in self.models}:
            self.models.append(_custom_model(model))
        await super().set_model(model, effort)

    async def set_mode(self, mode: str) -> None:
        if mode == "plan" and self.mode != "plan":
            self._mode_before_plan = self.mode
        await super().set_mode(mode)


def _user_text(message: dict) -> str:
    """The typed text of a user message, or "" for tool results and the like."""
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    else:
        return ""
    m = re.search(r"<command-name>(.*?)</command-name>", text)
    if m:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return (m.group(1) + " " + (args.group(1) if args else "")).strip()
    if text.startswith("<local-command") or text.startswith("<system-reminder>"):
        return ""
    return text.strip()


# Models Claude Code accepts with --model / set_model but leaves out of its
# picker (newer releases than the CLI's own list, and earlier versions).
# Each was checked with a one-line request on this account; one the CLI
# already lists is not repeated, so an updated CLI takes over.
_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
OTHER_MODELS = [
    {"id": i, "name": n, "resolved": i, "description": "", "efforts": e, "group": "Other versions"}
    for i, n, e in (
        ("claude-sonnet-5-5", "Sonnet 5.5", _EFFORTS),
        ("claude-haiku-5-5", "Haiku 5.5", _EFFORTS),
        ("claude-opus-5", "Opus 5", _EFFORTS),
        ("claude-sonnet-5", "Sonnet 5", _EFFORTS),
        ("claude-opus-4-8", "Opus 4.8", _EFFORTS),
        ("claude-opus-4-7", "Opus 4.7", _EFFORTS),
        ("claude-opus-4-6", "Opus 4.6", _EFFORTS),
        ("claude-sonnet-4-6", "Sonnet 4.6", _EFFORTS),
        ("claude-sonnet-4-5", "Sonnet 4.5", []),
        ("claude-haiku-4-5", "Haiku 4.5", []),
    )
]


def _model_entry(m: dict) -> dict:
    """A model as the picker shows it. Claude Code's display names are
    aliases ("Sonnet"); the version is in the description ("Sonnet 5 · …")."""
    desc = m.get("description") or ""
    version = desc.split(" · ")[0].replace(" with 1M context", " (1M)")
    name = m.get("displayName") or m["value"]
    if version and version.split()[0].lower() in name.lower():
        name = version  # "Sonnet" → "Sonnet 5"
    elif version:
        name = f"{name} · {version}"  # "Default (recommended) · Opus 5 (1M)"
    return {"id": m["value"], "name": name, "resolved": m.get("resolvedModel") or "",
            "description": desc.split(" · ", 1)[-1] if " · " in desc else desc,
            "efforts": m.get("supportedEffortLevels") or []}


def _custom_model(model_id: str) -> dict:
    return {"id": model_id, "name": model_id, "resolved": model_id, "description": "Custom model",
            "efforts": ["low", "medium", "high", "xhigh", "max"]}

