# dashboard/agents/acp.py
"""Agents that speak ACP (Agent Client Protocol): `kiro-cli acp`, and the
experimental ones in the catalog (Gemini CLI, Goose, OpenCode), which use
AcpSession as it is.

ACP lets the client offer file and terminal access, but kiro-cli reads,
writes and runs commands itself and only asks the client for permission, so
OopsBox advertises no fs/terminal capabilities and serves permission
requests only.

Kiro's own terminal UI is an ACP client too. Its slash commands go through
two extension methods rather than as prompt text, and so do ours:
  _kiro.dev/commands/execute  {sessionId, command: {command, args}} -> {success, message, data}
  _kiro.dev/commands/options  {sessionId, command, partial}         -> {options, hasMore}
"""
import asyncio
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .launch import build_command
from .rpc import JsonRpcProcess, RpcError
from .session import AgentSession, Attachment, CommandUnsupported, PermissionOption, clip, command

METHOD_NOT_FOUND = -32601

PROTOCOL_VERSION = 1

_STATUS = {
    "pending": "running",
    "in_progress": "running",
    "completed": "completed",
    "failed": "failed",
}

_OPTION_KIND = {
    "allow_once": "allow_once",
    "allow_always": "allow_always",
    "reject_once": "reject",
    "reject_always": "reject",
}

_OPTION_LABEL = {
    "allow_once": "Allow",
    "allow_always": "Always allow",
    "reject_once": "Deny",
    "reject_always": "Always deny",
}


def _content_text(content) -> str:
    """Flatten ACP ContentBlock / ToolCallContent lists into display text."""
    if not content:
        return ""
    if isinstance(content, dict):
        content = [content]
    parts = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "content":
            parts.append(_content_text(c.get("content")))
        elif c.get("type") == "diff":
            parts.append(f"--- {c.get('path', '')}\n{c.get('newText', '')}")
        elif c.get("type") == "text":
            parts.append(c.get("text", ""))
        elif c.get("type") == "terminal":
            parts.append(f"(terminal {c.get('terminalId', '')})")
    return "\n".join(p for p in parts if p)


class AcpSession(AgentSession):
    """Generic ACP driver; subclasses give the command line."""

    agent = ""
    command: list[str] = []
    # ACP has no system-prompt field, so memory instructions ride along with
    # the first message of a new session.
    instructions_in_first_prompt = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rpc: Optional[JsonRpcProcess] = None
        self._session_id: Optional[str] = None
        self._needs_instructions = False
        # message chunks arrive without ids; one assistant block per run of
        # chunks, closed when anything else happens
        self._msg_block: Optional[str] = None
        self._thought_block: Optional[str] = None

    async def _agent_start(self) -> None:
        argv, cwd, env = build_command(self.project, self.command + self.extra_args)
        self._rpc = JsonRpcProcess(argv, cwd, self._on_notification, self._on_request,
                                   self._on_exit, env=env)
        await self._rpc.start()
        init = await self._rpc.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
            "clientInfo": {"name": "oopsbox", "title": "OopsBox", "version": "2.0.0"},
        }, timeout=60) or {}
        caps = init.get("agentCapabilities") or {}
        can_load = bool(caps.get("loadSession"))
        self.supports_images = bool((caps.get("promptCapabilities") or {}).get("image"))
        cwd_param = str(self.project.workdir)
        saved = self.saved.get("session_id")
        if saved and can_load:
            try:
                # session/load replays the old conversation as session/update
                # notifications. Normally we already have it on disk, so they
                # are muted; after switching to another conversation they are
                # what fills the empty chat.
                self._replaying = not self._show_replay
                self._loading = True
                # set before loading: the replayed updates carry this id
                self._session_id = saved
                res = await self._rpc.request("session/load", {
                    "sessionId": saved, "cwd": cwd_param, "mcpServers": [],
                }, timeout=120) or {}
                await self._session_options(res)
                return
            except RpcError as e:
                hint = f" (if it is open in the Terminal tab, quit {self.agent} there, then switch back from 🕘)" \
                    if self.saved.get("handed_off") else ""
                self.notice(f"Could not resume the last conversation ({self.agent}: {e}); started a new conversation{hint}.", "warn")
            finally:
                self._replaying = False
                self._loading = False
                self._show_replay = False
                self._close_runs(persist=True)
        res = await self._rpc.request("session/new", {"cwd": cwd_param, "mcpServers": []}, timeout=120)
        self._session_id = res["sessionId"]
        self._needs_instructions = bool(self.instructions)
        self.save_state(session_id=self._session_id)
        await self._session_options(res)

    async def _session_options(self, res: dict) -> None:
        """Read the models and modes a session offers, and re-apply the
        user's earlier choice if the agent started on something else."""
        models = res.get("models") or {}
        self.models = [{"id": m["modelId"], "name": m.get("name") or m["modelId"],
                        "description": m.get("description", ""), "efforts": []}
                       for m in models.get("availableModels") or [] if m.get("modelId")]
        modes = res.get("modes") or {}
        self.modes = [{"id": m["id"], "name": m.get("name") or m["id"], "description": m.get("description", "")}
                      for m in modes.get("availableModes") or [] if m.get("id")]
        want_model, want_mode = self.model, self.mode
        self.model = models.get("currentModelId")
        self.mode = modes.get("currentModeId")
        if want_model and want_model != self.model and want_model in {m["id"] for m in self.models}:
            await self._try(self._agent_set_model(want_model, None))
        if want_mode and want_mode != self.mode and want_mode in {m["id"] for m in self.modes}:
            await self._try(self._agent_set_mode(want_mode))

    async def _try(self, coro) -> None:
        try:
            await coro
        except (RpcError, asyncio.TimeoutError) as e:
            self.notice(f"Setting failed: {e}", "warn")

    async def _agent_set_model(self, model, effort) -> None:
        await self._rpc.request("session/set_model", {"sessionId": self._session_id, "modelId": model}, timeout=30)
        self.model = model

    async def _agent_set_mode(self, mode: str) -> None:
        await self._rpc.request("session/set_mode", {"sessionId": self._session_id, "modeId": mode}, timeout=30)
        self.mode = mode

    _replaying = False
    _show_replay = False
    _loading = False
    _user_block: Optional[str] = None

    def _set_conversation_id(self, conversation_id: str) -> None:
        self.saved["session_id"] = conversation_id
        self._show_replay = True

    async def _agent_stop(self) -> None:
        if self._rpc:
            await self._rpc.close()
        self._rpc = None

    async def _agent_prompt(self, text: str, attachments: list[Attachment]) -> None:
        prompt = [{"type": "image", "data": a.b64, "mimeType": a.mime} for a in attachments]
        prompt.append({"type": "text", "text": text})
        if self._needs_instructions and self.instructions_in_first_prompt:
            prompt.insert(0, {"type": "text", "text": self.instructions})
            self._needs_instructions = False
        # session/prompt resolves when the turn ends, which can take many
        # minutes — wait for it in the background.
        self._rpc.spawn(self._run_turn(prompt))

    async def _run_turn(self, prompt: list) -> None:
        try:
            res = await self._rpc.request("session/prompt", {
                "sessionId": self._session_id, "prompt": prompt,
            }) or {}
            reason = res.get("stopReason")
            if reason == "cancelled":
                self.notice("Interrupted.")
            elif reason in ("max_tokens", "max_turn_requests", "refusal"):
                self.notice(f"Turn ended: {reason}", "warn")
        except (RpcError, ConnectionError) as e:
            if self.alive:
                self.notice(f"Turn failed: {e}", "error")
        finally:
            self._close_runs(persist=True)
            if self.status == "working":
                self.set_status("idle")

    async def _agent_cancel(self) -> None:
        if self._rpc and self._session_id:
            await self._rpc.notify("session/cancel", {"sessionId": self._session_id})

    async def _on_exit(self, code, stderr_tail) -> None:
        self._rpc = None
        self.agent_exited(code, stderr_tail)

    # ── session/update ────────────────────────────────
    def _new_id(self, prefix: str) -> str:
        # random, not a counter: a resumed session keeps its id, and a counter
        # restarting at 1 would overwrite blocks from before the restart
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    def _close_runs(self, persist: bool) -> None:
        for attr in ("_msg_block", "_thought_block", "_user_block"):
            bid = getattr(self, attr)
            if bid and bid in self.blocks and persist:
                self.upsert({"id": bid})  # re-upsert to persist the full text
            setattr(self, attr, None)

    async def _on_notification(self, method: str, p: dict) -> None:
        if method != "session/update" or self._replaying:
            return
        if p.get("sessionId") not in (None, self._session_id):
            return
        u = p.get("update") or {}
        kind = u.get("sessionUpdate")
        if kind != "user_message_chunk" and self._user_block:
            self.upsert({"id": self._user_block})
            self._user_block = None
        if kind == "agent_message_chunk":
            if self._thought_block:
                if self._thought_block in self.blocks:
                    self.upsert({"id": self._thought_block})
                self._thought_block = None
            if not self._msg_block:
                self._msg_block = self._new_id("m")
            self.append_text(self._msg_block, "text", _content_text(u.get("content")))
        elif kind == "agent_thought_chunk":
            if not self._thought_block:
                self._thought_block = self._new_id("t")
            self.append_text(self._thought_block, "text", _content_text(u.get("content")), kind="thought")
        elif kind in ("tool_call", "tool_call_update"):
            self._close_runs(persist=True)
            tid = "tc-" + str(u.get("toolCallId"))
            status = _STATUS.get(u.get("status"), "running" if kind == "tool_call" else None)
            block: dict = {"id": tid, "kind": "tool"}
            if u.get("title"):
                block["title"] = u["title"]
            if u.get("kind"):
                block["tool"] = {"read": "read", "edit": "edit", "delete": "edit", "move": "edit",
                                 "execute": "execute", "search": "search", "fetch": "search"
                                 }.get(u["kind"], "other")
            if status:
                block["status"] = status
            raw = u.get("rawInput")
            if raw and tid not in self.blocks:
                block["detail"] = clip(json.dumps(raw, ensure_ascii=False, indent=2))
            text = _content_text(u.get("content"))
            if text:
                block["output"] = clip(text)
            if tid not in self.blocks:
                block.setdefault("title", "tool")
                block.setdefault("tool", "other")
                block.setdefault("status", "running")
            self.upsert(block, persist=block.get("status") in ("completed", "failed"))
        elif kind == "plan":
            entries = u.get("entries") or []
            marks = {"completed": "☑", "in_progress": "▶", "pending": "☐"}
            text = "\n".join(f"{marks.get(e.get('status'), '•')} {e.get('content', '')}" for e in entries)
            self.upsert({"id": f"plan-{self._session_id}", "kind": "plan", "text": text})
        elif kind == "user_message_chunk":
            # live, these echo what we sent; replayed, they are the history
            if self._loading:
                for attr in ("_msg_block", "_thought_block"):
                    bid = getattr(self, attr)
                    if bid in self.blocks:
                        self.upsert({"id": bid})
                    setattr(self, attr, None)
                if not self._user_block:
                    self._user_block = self._new_id("u")
                self.append_text(self._user_block, "text", _content_text(u.get("content")), kind="user")
        elif kind == "current_mode_update":
            self.mode = u.get("currentModeId") or self.mode
            self.publish_state()
        elif kind == "available_commands_update":
            self.commands = [{"name": "/" + c["name"].lstrip("/"), "description": c.get("description", ""),
                              "hint": ((c.get("input") or {}).get("hint") or "")}
                             for c in u.get("availableCommands") or [] if c.get("name")]
            self.publish_state()

    # ── requests ──────────────────────────────────────
    async def _on_request(self, method: str, p: dict) -> Any:
        if method == "session/request_permission":
            self._close_runs(persist=True)
            tc = p.get("toolCall") or {}
            block = self.blocks.get("tc-" + str(tc.get("toolCallId")), {})
            title = tc.get("title") or block.get("title") or "Permission request"
            detail = block.get("detail") or _content_text(tc.get("content"))
            options = [
                PermissionOption(o["optionId"], _OPTION_LABEL.get(o.get("kind"), o.get("name", o["optionId"])),
                                 _OPTION_KIND.get(o.get("kind"), "reject"))
                for o in p.get("options") or []
            ]
            choice = await self.ask_permission("perm-" + str(tc.get("toolCallId")), title, detail, options)
            if not choice:
                return {"outcome": {"outcome": "cancelled"}}
            return {"outcome": {"outcome": "selected", "optionId": choice}}
        raise RpcError({"code": -32601, "message": f"OopsBox does not handle {method}"})


# Commands whose kiro implementation needs its own terminal UI (a clipboard,
# $EDITOR, a microphone), and what the browser does instead.
_KIRO_LOCAL = {"/paste", "/reply", "/quit", "/chat"}
_KIRO_TERMINAL = {"/voice"}


class KiroSession(AcpSession):
    agent = "kiro"
    command = ["kiro-cli", "acp"]
    can_steer = True
    replays_history = True
    reserved_flags = frozenset({"-h", "--help"})
    known_flags = [
        {"flag": "--agent", "arg": "<name>", "description": "Agent config to start with"},
        {"flag": "--model", "arg": "<id>", "description": "Model to start with"},
        {"flag": "--effort", "arg": "<level>", "description": "Reasoning effort: low, medium, high, xhigh, max"},
        {"flag": "--trust-all-tools", "arg": "", "description": "Never ask for any tool (same as Autopilot)"},
        {"flag": "--trust-tools", "arg": "<names>", "description": "Trust only these tools, comma-separated"},
        {"flag": "--agent-engine", "arg": "v1|v2|v3", "description": "Agent engine version (default v2)"},
        {"flag": "--verbose", "arg": "", "description": "More verbose logs"},
    ]

    # ── Slash commands ────────────────────────────────
    def _kiro_commands(self, raw: list[dict]) -> list[dict]:
        out = []
        for c in raw:
            name = "/" + str(c.get("name", "")).lstrip("/")
            if name == "/":
                continue
            meta = c.get("meta") or {}
            if name in _KIRO_TERMINAL:
                kind = "terminal"
            elif name in _KIRO_LOCAL or meta.get("local"):
                kind = "local"
            else:
                kind = {"selection": "selection", "panel": "panel"}.get(meta.get("inputType"), "action")
            descs = meta.get("subcommandDescriptions") or {}
            hints = meta.get("subcommandHints") or {}
            subs = [{"name": sc, "hint": hints.get(sc, ""), "description": descs.get(sc, "")}
                    for sc in meta.get("subcommands") or []]
            out.append(command(name, c.get("description", ""), kind, meta.get("hint", ""), subs))
        out.append(command("/new", "Start a new conversation (the old one stays on the server)", "local"))
        return out

    async def _agent_command(self, name: str, value: str) -> dict:
        if name == "/rewind" and value:
            return await self._rewind(value)
        args = {"value": value} if value else {}
        if value.startswith("{"):
            # a panel's button can pass kiro's structured arguments as JSON
            # (/rewind takes {turnIndex, messageId})
            try:
                args = json.loads(value)
            except json.JSONDecodeError:
                pass
        res = await self._ext("_kiro.dev/commands/execute",
                              {"sessionId": self._session_id,
                               "command": {"command": name.lstrip("/"), "args": args}}, timeout=300)
        if name in ("/model", "/agent", "/effort") and res.get("success"):
            # the session's model/mode lists follow these
            self._spawn(self._refresh_after_switch())
        return {"ok": bool(res.get("success", True)), "message": res.get("message") or "",
                "data": res.get("data")}

    async def _rewind(self, value: str) -> dict:
        """Fork the conversation before a turn and continue on the fork.

        The turn is named by the logIndex `/rewind` lists (sent as a string;
        kiro-cli 2.18 exits on a number). The fork is a new session.
        """
        try:
            index = int(json.loads(value)["turnIndex"]) if value.startswith("{") else int(value)
        except (ValueError, KeyError, TypeError):
            return {"ok": False, "message": "Usage: /rewind <logIndex>, or run /rewind to pick from a list"}
        res = await self._ext("_kiro.dev/commands/execute", {"sessionId": self._session_id, "command": {
            "command": "rewind", "args": {"turnIndex": str(index)}}}, timeout=120)
        new_id = (res.get("data") or {}).get("sessionId")
        if not res.get("success") or not new_id:
            return {"ok": False, "message": res.get("message") or "Rewind failed"}
        self._spawn(self.resume_conversation(new_id))
        return {"message": "Went back to before that turn; continuing in a new branched conversation (the original is still in 🕘)."}

    async def _agent_command_options(self, name: str, partial: str) -> list[dict]:
        res = await self._ext("_kiro.dev/commands/options",
                              {"sessionId": self._session_id, "command": name.lstrip("/"),
                               "partial": partial}, timeout=60)
        out = []
        for o in res.get("options") or []:
            desc = o.get("description") or ""
            active = "[active]" in desc
            out.append({"value": o.get("value"), "label": o.get("label") or o.get("value"),
                        "description": desc.replace("[active]", "").strip(),
                        "group": o.get("group") or "", "active": active})
        return out

    async def _ext(self, method: str, params: dict, timeout: float) -> dict:
        if not self._rpc or not self._session_id:
            raise RuntimeError("kiro is not running")
        try:
            return await self._rpc.request(method, params, timeout=timeout) or {}
        except RpcError as e:
            if e.code == METHOD_NOT_FOUND:
                raise CommandUnsupported() from e
            raise

    async def _refresh_after_switch(self) -> None:
        await asyncio.sleep(0.5)
        try:
            opts = await self._agent_command_options("/model", "")
        except Exception:
            return
        current = next((o["value"] for o in opts if o["active"]), None)
        if current:
            self.model = current
            self.publish_state()

    async def _local_command(self, name: str, value: str) -> dict:
        if name == "/quit":
            self._spawn(self.stop())
            return {"message": "kiro stopped."}
        if name == "/paste":
            return {"message": "Paste an image into the input box, or attach one with 📎."}
        if name == "/reply":
            last = next((b for b in reversed(list(self.blocks.values())) if b["kind"] == "assistant"), None)
            return {"message": "Put the last reply in the input box." if last else "No reply to quote yet.",
                    "data": {"fill": "> " + "\n> ".join((last or {}).get("text", "").splitlines())} if last else None}
        if name == "/chat":
            sub, _, rest = value.partition(" ")
            if sub == "new" or value == "new":
                await self.new_conversation()
                if rest.strip():
                    await self.prompt(rest.strip())
                return {"message": "Started a new conversation."}
            if not value:
                return {"message": "", "data": {"open": "conversations"}}
            raise CommandUnsupported()  # save/load <path>: files on the kiro host
        return await super()._local_command(name, value)

    # ── Steering a running turn ───────────────────────
    async def _agent_steer(self, text: str, attachments: list[Attachment]) -> None:
        try:
            await self._ext("_session/steer", {"sessionId": self._session_id, "message": text}, timeout=30)
        except (CommandUnsupported, RpcError):
            # an agent without steering takes the message after this turn
            self.can_steer = False
            block = next(b for b in reversed(list(self.blocks.values())) if b["kind"] == "user")
            self.upsert({"id": block["id"], "queued": True})
            self._queue.append((block["id"], text, attachments))
            self.publish_state()

    # ── Kiro notifications ────────────────────────────
    async def _on_notification(self, method: str, p: dict) -> None:
        if not method.startswith("_kiro.dev/"):
            return await super()._on_notification(method, p)
        if p.get("sessionId") not in (None, self._session_id):
            return
        kind = method[len("_kiro.dev/"):]
        if kind == "commands/available":
            self.commands = self._kiro_commands(p.get("commands") or [])
            self.publish_state()
        elif kind == "metadata":
            pct = p.get("contextUsagePercentage")
            if pct is not None:
                self.set_usage(float(pct), f"context {pct:.0f}%")
        elif kind == "compaction/status":
            st = p.get("status") or {}
            if st.get("type") == "failed":
                self.notice(f"Compaction failed: {st.get('error') or ''}", "error")
            elif st.get("type") in ("completed", "complete", "done", "success"):
                self.notice("Conversation compacted.")
        elif kind == "clear/status":
            self.notice("Conversation history cleared.")
        elif kind == "agent/switched":
            name = p.get("agentName")
            self.notice(f"Switched to agent {name}." + (f"\n{p['welcomeMessage']}" if p.get("welcomeMessage") else ""))
            if name and any(m["id"] == name for m in self.modes):
                self.mode = name
            if p.get("model"):
                self.model = p["model"]
            self.publish_state()
        elif kind == "agent/not_found":
            self.notice(f"Agent {p.get('requestedAgent', '')} not found; using {p.get('fallbackAgent', '')}.", "warn")
        elif kind == "agent/config_error":
            self.notice(f"Agent config error ({p.get('path', '')}): {p.get('error', '')}", "error")
        elif kind == "error/rate_limit":
            self.notice(f"Usage limit reached: {p.get('message', '')}", "warn")
        elif kind == "mcp/oauth_request":
            self.notice(f"MCP server {p.get('serverName', '')} needs a login; open: {p.get('oauthUrl', '')}", "warn")
        elif kind == "mcp/server_init_failure":
            self.notice(f"MCP server {p.get('serverName', '')} failed to start: {p.get('error', '')}", "warn")
        elif kind == "goal/status":
            it = f" ({p['iteration']}/{p.get('maxIterations', '?')})" if p.get("iteration") is not None else ""
            self.notice(f"Goal {p.get('state', '')}{it}: {p.get('message', '')}")

    # ── Other conversations ───────────────────────────
    @property
    def lists_conversations(self) -> bool:
        return True

    async def list_conversations(self) -> list[dict]:
        root = Path.home() / ".kiro" / "sessions" / "cli"
        want = str(self.project.workdir.resolve())
        out = []
        for f in root.glob("*.json"):
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                same = str(Path(meta.get("cwd", "")).resolve()) == want
            except OSError:
                same = False
            if not same:
                continue
            out.append({"id": meta.get("session_id") or f.stem, "title": meta.get("title") or "",
                        "updated": _epoch(meta.get("updated_at")),
                        "current": (meta.get("session_id") or f.stem) == self.saved.get("session_id")})
        return sorted(out, key=lambda c: c["updated"], reverse=True)

    def terminal_command(self) -> Optional[list[str]]:
        sid = self.saved.get("session_id")
        return ["kiro-cli", "chat"] + (["--resume-id", sid] if sid else [])


def _epoch(ts: Optional[str]) -> float:
    if not ts:
        return 0.0
    # kiro writes nanoseconds; fromisoformat takes at most microseconds
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0
