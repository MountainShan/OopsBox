# dashboard/agents/codex.py
"""Codex, driven through `codex app-server` (JSON-RPC over stdio).

Protocol reference: `codex app-server generate-json-schema --experimental
--out DIR`. OopsBox opts into the experimental API, as codex's own TUI does:
plan mode, mid-thread settings, background terminals and memory live there.
The thread runs in the workspace-write sandbox with approval "on-request":
codex works inside the project freely and asks before anything that needs
to leave the sandbox — those questions become permission blocks.

The TUI's slash commands map onto app-server methods (COMMANDS below);
the few that only make sense in a terminal are handed to the Terminal tab.
"""
import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from .launch import build_command
from .rpc import JsonRpcProcess, RpcError
from .session import (AgentSession, Attachment, CommandUnsupported, PermissionOption, clip,
                      command)

# Modes are codex's approval/sandbox presets, as in its /approvals picker.
MODES = [
    {"id": "auto", "name": "Auto", "description": "Read and write the project folder; ask when going beyond it",
     "approval": "on-request", "sandbox": "workspace-write", "policy": {"type": "workspaceWrite"}},
    {"id": "read-only", "name": "Read only", "description": "Read only; ask before any change",
     "approval": "on-request", "sandbox": "read-only", "policy": {"type": "readOnly"}},
    {"id": "full", "name": "Full access", "description": "No sandbox, no prompts; can access the whole machine",
     "approval": "never", "sandbox": "danger-full-access", "policy": {"type": "dangerFullAccess"}},
]
_MODE = {m["id"]: m for m in MODES}
DEFAULT_MODE = "auto"

# codex's TUI slash commands, with what each does here
COMMANDS = [
    command("model", "Choose the model", "selection"),
    command("permissions", "What codex is allowed to do (approvals and sandbox)", "selection"),
    command("approvals", "Same as /permissions", "selection"),
    command("plan", "Toggle Plan mode (plan first, no changes)", "action"),
    command("personality", "Codex communication style", "selection"),
    command("fast", "Toggle Fast mode", "action"),
    command("status", "Current settings, account, and usage", "panel"),
    command("usage", "Account usage and limits", "panel"),
    command("debug-config", "Where each config layer comes from", "panel"),
    command("mcp", "MCP servers and tools", "panel", "[reload|login <name>]",
            [{"name": "reload", "hint": "", "description": "Reload the MCP config"},
             {"name": "login", "hint": "<name>", "description": "Log in to a server that needs OAuth"}]),
    command("skills", "Skills", "panel", "[enable|disable <name>]",
            [{"name": "enable", "hint": "<name>", "description": "Enable a skill"},
             {"name": "disable", "hint": "<name>", "description": "Disable a skill"}]),
    command("plugins", "Plugins", "panel", "[install <name>|uninstall <id>]",
            [{"name": "install", "hint": "<name>", "description": "Install a plugin"},
             {"name": "uninstall", "hint": "<id>", "description": "Uninstall a plugin"}]),
    command("apps", "Connected apps", "panel"),
    command("hooks", "Lifecycle hooks", "panel"),
    command("experimental", "Experimental feature toggles", "panel", "[enable|disable <name>]",
            [{"name": "enable", "hint": "<name>", "description": "Turn on"},
             {"name": "disable", "hint": "<name>", "description": "Turn off"}]),
    command("memories", "Memory: whether this conversation uses and creates memories", "selection"),
    command("goal", "Set or view the goal for a long task", "panel", "[<goal>|clear|pause|resume]",
            [{"name": "clear", "hint": "", "description": "Clear the goal"},
             {"name": "pause", "hint": "", "description": "Pause"},
             {"name": "resume", "hint": "", "description": "Resume"}]),
    command("new", "Start a new conversation", "local"),
    command("clear", "Start a new conversation", "local"),
    command("resume", "Resume a previous conversation", "local"),
    command("fork", "Fork a new conversation from the current one", "action"),
    command("rename", "Rename the conversation", "action", "<name>"),
    command("archive", "Archive this conversation and start a new one", "action"),
    command("delete", "Permanently delete this conversation and start a new one", "action"),
    command("compact", "Compact the conversation to free up context", "action"),
    command("review", "Review changes", "action", "[branch <base>|commit <sha>|<instructions>]"),
    command("init", "Create AGENTS.md", "local"),
    command("diff", "git diff (including untracked files)", "panel"),
    command("mention", "Mention a file (type @ in the input box)", "local"),
    command("ps", "Background terminals", "panel"),
    command("stop", "Stop all background terminals", "action"),
    command("copy", "Copy the last reply", "local"),
    command("rollout", "Show the path of the conversation log file", "action"),
    command("logout", "Log out of codex", "action"),
    command("quit", "Stop codex", "local"),
    command("exit", "Stop codex", "local"),
    *[command(n, d, "terminal") for n, d in (
        ("feedback", "Report an issue to the codex team"), ("import", "Import settings from Claude Code"),
        ("side", "Side conversation"), ("agent", "Switch to a sub-agent conversation"), ("approve", "Retry an action denied by auto review"),
        ("keymap", "Keyboard shortcuts"), ("title", "Terminal title"), ("statusline", "Status line"), ("theme", "Syntax colors"),
        ("pet", "Terminal pet"), ("raw", "Raw scroll mode"), ("ide", "IDE context"), ("app", "Continue in the desktop app"))],
]

PERSONALITIES = [
    {"value": "none", "label": "none", "description": "No preference"},
    {"value": "friendly", "label": "friendly", "description": "Friendly"},
    {"value": "pragmatic", "label": "pragmatic", "description": "Pragmatic"},
]

# codex's own /init prompt (codex-cli 0.147)
INIT_PROMPT = """Generate a file named AGENTS.md that serves as a contributor guide for this repository.
Before writing, check whether AGENTS.md already exists in the current working directory. If it does, do not overwrite or modify it.
Your goal is to produce a clear, concise, and well-structured document with descriptive headings and actionable explanations for each section.
Follow the outline below, but adapt as needed — add sections if relevant, and omit those that do not apply to this project.

Document Requirements

- Title the document "Repository Guidelines".
- Use Markdown headings (#, ##, etc.) for structure.
- Keep the document concise. 200-400 words is optimal.
- Keep explanations short, direct, and specific to this repository.
- Provide examples where helpful (commands, directory paths, naming patterns).
- Maintain a professional, instructional tone.

Recommended Sections

Project Structure & Module Organization

- Outline the project structure, including where the source code, tests, and assets are located.

Build, Test, and Development Commands

- List key commands for building, testing, and running locally (e.g., npm test, make build).
- Briefly explain what each command does.

Coding Style & Naming Conventions

- Specify indentation rules, language-specific style preferences, and naming patterns.
- Include any formatting or linting tools used.

Testing Guidelines

- Identify testing frameworks and coverage requirements.
- State test naming conventions and how to run tests.

Commit & Pull Request Guidelines

- Summarize commit message conventions found in the project’s Git history.
- Outline pull request requirements (descriptions, linked issues, screenshots, etc.).

(Optional) Add other sections if relevant, such as Security & Configuration Tips, Architecture Overview, or Agent-Specific Instructions."""

_STATUS = {
    "inProgress": "running",
    "completed": "completed",
    "failed": "failed",
    "declined": "declined",
}

# `/bin/bash -lc 'cat x'` → `cat x`: the wrapper is codex's, not the agent's.
# Codex quotes with single quotes, or double quotes when the command itself
# contains single quotes.
_BASH_WRAPPER = re.compile(r"^(?:/usr)?/bin/(?:ba|z)?sh -lc (?:'(?P<sq>.*)'|\"(?P<dq>.*)\")$", re.S)
_DQ_ESCAPE = re.compile(r'\\([\\"$`])')

_ALLOW = [
    PermissionOption("accept", "Allow", "allow_once"),
    PermissionOption("acceptForSession", "Allow for this conversation", "allow_always"),
    PermissionOption("decline", "Deny", "reject"),
]


def _command_text(cmd: Optional[str]) -> str:
    if not cmd:
        return ""
    m = _BASH_WRAPPER.match(cmd)
    if not m:
        return cmd
    if m.group("sq") is not None:
        return m.group("sq").replace("'\\''", "'")
    return _DQ_ESCAPE.sub(r"\1", m.group("dq"))


class CodexSession(AgentSession):
    agent = "codex"
    command = ["codex", "app-server"]
    can_steer = True
    lists_conversations = True
    reserved_flags = frozenset({"--listen", "--stdio", "-h", "--help"})
    known_flags = [
        {"flag": "-c", "arg": "key=value", "description": "Override any config.toml setting (repeatable)"},
        {"flag": "--enable", "arg": "<feature>", "description": "Enable a feature flag (repeatable)"},
        {"flag": "--disable", "arg": "<feature>", "description": "Disable a feature flag (repeatable)"},
        {"flag": "--strict-config", "arg": "", "description": "Fail on unknown fields in config.toml"},
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rpc: Optional[JsonRpcProcess] = None
        self._thread_id: Optional[str] = None
        self._turn_id: Optional[str] = None

    # ── Lifecycle ─────────────────────────────────────
    async def _agent_start(self) -> None:
        argv, cwd, env = build_command(self.project, self.command + self.extra_args)
        self._rpc = JsonRpcProcess(
            argv, cwd, self._on_notification, self._on_request, self._on_exit,
            env=env, jsonrpc_field=False,
        )
        await self._rpc.start()
        await self._rpc.request("initialize", {
            "clientInfo": {"name": "oopsbox", "title": "OopsBox", "version": "2.0.0"},
            "capabilities": {"experimentalApi": True},
        }, timeout=60)
        await self._rpc.notify("initialized", {})

        self.modes = [{k: m[k] for k in ("id", "name", "description")} for m in MODES]
        self.commands = [dict(c) for c in COMMANDS]
        self.supports_images = True
        if self.mode not in _MODE:
            self.mode = DEFAULT_MODE
        mode = _MODE[self.mode]
        params = {
            "approvalPolicy": mode["approval"],
            "sandbox": mode["sandbox"],
            "developerInstructions": self.instructions or None,
        }
        if self.model:
            params["model"] = self.model
        if not self.project.is_ssh:
            params["cwd"] = str(self.project.path)
        res = None
        saved = self.saved.get("thread_id")
        if saved:
            try:
                res = await self._rpc.request("thread/resume", {"threadId": saved, **params}, timeout=60)
                self.save_state(on_disk=saved)
            except (RpcError, KeyError) as e:
                # codex writes a thread to disk only once it has a turn; an
                # empty one has nothing to lose, so start over quietly
                if "no rollout found" not in str(e):
                    self.notice(f"Could not resume the last conversation ({e}); starting a new one.")
        if res is None:
            res = await self._rpc.request("thread/start", params, timeout=60)
        self._thread_id = res["thread"]["id"]
        self.save_state(thread_id=self._thread_id)
        if self._show_history:
            self._show_history = False
            await self._render_history()
        self.model = self.model or res.get("model")
        self.effort = self.effort or res.get("reasoningEffort")
        await self._load_models()

    async def _load_models(self) -> None:
        try:
            res = await self._rpc.request("model/list", {}, timeout=30) or {}
        except (RpcError, asyncio.TimeoutError):
            return
        self.models = [{
            "id": m["id"],
            "name": m.get("displayName") or m["id"],
            "description": m.get("description", ""),
            "efforts": [e.get("reasoningEffort") for e in m.get("supportedReasoningEfforts") or []
                        if isinstance(e, dict)],
            "default_effort": m.get("defaultReasoningEffort"),
        } for m in res.get("data") or [] if not m.get("hidden")]

    async def _agent_stop(self) -> None:
        if self._rpc:
            await self._rpc.close()
        self._rpc = None
        self._turn_id = None

    def _input(self, text: str, attachments: list[Attachment]) -> list[dict]:
        items = []
        for a in attachments:
            # a remote codex cannot open a path on the OopsBox host
            items.append({"type": "image", "url": a.data_url} if self.project.is_ssh
                         else {"type": "localImage", "path": str(a.path)})
        items.append({"type": "text", "text": text})
        return items

    def _turn_settings(self) -> dict:
        """Settings the TUI keeps per thread: plan mode, personality, speed."""
        out: dict = {}
        if self.saved.get("plan"):
            out["collaborationMode"] = {"mode": "plan", "settings": {
                "model": self.model or "", "reasoning_effort": self.effort}}
        if self.saved.get("personality"):
            out["personality"] = self.saved["personality"]
        if self.saved.get("fast"):
            out["serviceTier"] = "fast"
        return out

    async def _agent_set_model(self, model, effort) -> None:
        # applies to this thread from now on, not only to the next turn
        await self._settings_update({"model": model, "effort": effort})

    async def _settings_update(self, changes: dict) -> None:
        if not self._rpc or not self._thread_id:
            return
        try:
            await self._rpc.request("thread/settings/update",
                                    {"threadId": self._thread_id, **changes}, timeout=30)
        except RpcError as e:
            if e.code != -32601:
                raise

    async def _agent_prompt(self, text: str, attachments: list[Attachment]) -> None:
        mode = _MODE.get(self.mode or DEFAULT_MODE, _MODE[DEFAULT_MODE])
        params = {
            "threadId": self._thread_id,
            "input": self._input(text, attachments),
            "approvalPolicy": mode["approval"],
            "sandboxPolicy": mode["policy"],
        }
        if self.model:
            params["model"] = self.model
        if self.effort:
            params["effort"] = self.effort
        params.update(self._turn_settings())
        res = await self._rpc.request("turn/start", params, timeout=60)
        self._turn_id = (res or {}).get("turn", {}).get("id") or self._turn_id

    async def _agent_steer(self, text: str, attachments: list[Attachment]) -> None:
        if not self._turn_id:
            raise RuntimeError("no running turn to add to")
        await self._rpc.request("turn/steer", {
            "threadId": self._thread_id, "expectedTurnId": self._turn_id,
            "input": self._input(text, attachments),
        }, timeout=30)

    async def _agent_cancel(self) -> None:
        if self._rpc and self._thread_id and self._turn_id:
            await self._rpc.request("turn/interrupt", {
                "threadId": self._thread_id, "turnId": self._turn_id,
            }, timeout=30)

    async def _on_exit(self, code, stderr_tail) -> None:
        self._rpc = None
        self.agent_exited(code, stderr_tail)

    # ── Slash commands ────────────────────────────────
    async def _call(self, method: str, params: Optional[dict] = None, timeout: float = 60) -> dict:
        if not self._rpc:
            raise RuntimeError("codex is not running")
        try:
            return await self._rpc.request(method, params, timeout=timeout) or {}
        except RpcError as e:
            if e.code == -32601:
                raise CommandUnsupported() from e
            raise

    async def _agent_command_options(self, name: str, partial: str) -> list[dict]:
        if name == "/model":
            return [{"value": m["id"], "label": m["name"], "description": m.get("description", ""),
                     "group": "", "active": m["id"] == self.model} for m in self.models]
        if name in ("/permissions", "/approvals"):
            return [{"value": m["id"], "label": m["name"], "description": m["description"],
                     "group": "", "active": m["id"] == self.mode} for m in MODES]
        if name == "/personality":
            current = self.saved.get("personality") or "none"
            return [{**o, "group": "", "active": o["value"] == current} for o in PERSONALITIES]
        if name == "/memories":
            current = self.saved.get("memory_mode", "enabled")
            return [{"value": "enabled", "label": "Use memory", "description": "This conversation reads and creates memories",
                     "group": "", "active": current == "enabled"},
                    {"value": "disabled", "label": "Don't use memory", "description": "This conversation neither reads nor writes memories",
                     "group": "", "active": current == "disabled"},
                    {"value": "reset", "label": "Clear all memories", "description": "Delete the memories codex has built up",
                     "group": "Danger", "active": False}]
        return []

    async def _agent_command(self, name: str, value: str) -> dict:
        tid = self._thread_id
        sub, _, rest = value.partition(" ")
        rest = rest.strip()
        cwd = str(self.project.path) if not self.project.is_ssh else None

        if name == "/model":
            await self.set_model(value, None)
            return {"message": f"Model changed to {value}."}
        if name in ("/permissions", "/approvals"):
            await self.set_mode(value)
            return {"message": f"Permissions changed to \"{_MODE[value]['name']}\"."}
        if name == "/plan":
            on = not self.saved.get("plan")
            self.save_state(plan=on)
            await self._settings_update({"collaborationMode": {"mode": "plan" if on else "default",
                                         "settings": {"model": self.model or "", "reasoning_effort": self.effort}}})
            self.publish_state()
            return {"message": "Switched to Plan mode: codex will plan first and won't modify files." if on else "Left Plan mode."}
        if name == "/personality":
            self.save_state(personality=None if value == "none" else value)
            await self._settings_update({"personality": value})
            return {"message": f"Communication style changed to {value}."}
        if name == "/fast":
            on = not self.saved.get("fast")
            self.save_state(fast=on)
            await self._settings_update({"serviceTier": "fast" if on else None})
            return {"message": "Fast mode on (uses more quota)." if on else "Fast mode off."}
        if name == "/memories":
            if value == "reset":
                await self._call("memory/reset")
                return {"message": "Cleared codex's memories."}
            await self._call("thread/memoryMode/set", {"threadId": tid, "mode": value})
            self.save_state(memory_mode=value)
            return {"message": "This conversation will use memory." if value == "enabled" else "This conversation won't use memory."}

        if name == "/status":
            cfg = (await self._call("config/read", {"cwd": cwd})).get("config") or {}
            acct = await self._call("account/read", {})
            limits = (await self._call("account/rateLimits/read")).get("rateLimits") or {}
            mode = _MODE.get(self.mode or DEFAULT_MODE)
            return {"data": {
                "model": self.model, "effort": self.effort, "permissions": mode["name"] if mode else self.mode,
                "plan_mode": bool(self.saved.get("plan")), "fast": bool(self.saved.get("fast")),
                "personality": self.saved.get("personality") or "none",
                "thread": tid, "cwd": cwd, "context": self.usage,
                "account": acct.get("account"), "rate_limits": limits,
                "config": {k: v for k, v in cfg.items() if v is not None},
            }}
        if name == "/usage":
            usage = await self._call("account/usage/read")
            limits = (await self._call("account/rateLimits/read")).get("rateLimits") or {}
            return {"data": {"rate_limits": limits, "summary": usage.get("summary"),
                             "daily": (usage.get("dailyUsageBuckets") or [])[-14:]}}
        if name == "/debug-config":
            cfg = await self._call("config/read", {"cwd": cwd, "includeLayers": True})
            req = await self._call("configRequirements/read")
            return {"data": {"origins": cfg.get("origins"), "layers": cfg.get("layers"),
                             "requirements": req.get("requirements")}}
        if name == "/mcp":
            if sub == "reload":
                await self._call("config/mcpServer/reload")
                return {"message": "Reloaded the MCP config."}
            if sub == "login" and rest:
                res = await self._call("mcpServer/oauth/login", {"name": rest, "threadId": tid}, timeout=300)
                url = res.get("authorizationUrl") or res.get("url") or ""
                return {"message": f"Open this URL to log in to {rest}: {url}" if url else f"Logged in to {rest}.", "data": res or None}
            res = await self._call("mcpServerStatus/list", {"threadId": tid, "detail": "toolsAndAuthOnly"})
            return {"data": {"servers": [{"name": m.get("name"), "auth": m.get("authStatus") or "",
                                          "tools": sorted((m.get("tools") or {}).keys())}
                                         for m in res.get("data") or []]}}
        if name == "/skills":
            if sub in ("enable", "disable") and rest:
                await self._call("skills/config/write", {"enabled": sub == "enable", "name": rest})
                return {"message": f"Skill {rest} {'enabled' if sub == 'enable' else 'disabled'}."}
            res = await self._call("skills/list", {"cwds": [cwd] if cwd else []})
            skills = [sk for group in res.get("data") or [] for sk in group.get("skills") or []]
            return {"data": {"skills": [{"name": sk.get("name"), "enabled": sk.get("enabled"),
                                         "scope": sk.get("scope"),
                                         "description": (sk.get("interface") or {}).get("shortDescription")
                                         or sk.get("description", "")} for sk in skills]}}
        if name == "/plugins":
            if sub == "install" and rest:
                await self._call("plugin/install", {"pluginName": rest}, timeout=300)
                return {"message": f"Installed {rest}."}
            if sub == "uninstall" and rest:
                await self._call("plugin/uninstall", {"pluginId": rest}, timeout=120)
                return {"message": f"Uninstalled {rest}."}
            res = await self._call("plugin/list", {"cwds": [cwd] if cwd else []}, timeout=120)
            installed, available = [], 0
            for mk in res.get("marketplaces") or []:
                for pl in mk.get("plugins") or []:
                    if pl.get("installed"):
                        installed.append({"id": pl.get("id"), "name": pl.get("name"),
                                          "enabled": pl.get("enabled"), "version": pl.get("localVersion") or pl.get("version")})
                    else:
                        available += 1
            return {"message": f"{len(installed)} installed, {available} more available (/plugins install <name>).",
                    "data": {"installed": installed}}
        if name == "/apps":
            res = await self._call("app/list", {"threadId": tid, "limit": 100}, timeout=120)
            return {"data": {"apps": [{"name": a.get("name"), "description": a.get("description"),
                                       "linked": a.get("isAccessible", a.get("linked"))}
                                      for a in res.get("data") or []]}}
        if name == "/hooks":
            res = await self._call("hooks/list", {"cwds": [cwd] if cwd else []})
            return {"data": {"hooks": [h for g in res.get("data") or [] for h in g.get("hooks") or []],
                             "warnings": [w for g in res.get("data") or [] for w in g.get("warnings") or []]}}
        if name == "/experimental":
            if sub in ("enable", "disable") and rest:
                await self._call("experimentalFeature/enablement/set", {"enablement": {rest: sub == "enable"}})
                return {"message": f"{rest} turned {'on' if sub == 'enable' else 'off'}; takes effect after restarting codex."}
            res = await self._call("experimentalFeature/list", {"threadId": tid, "limit": 500})
            return {"data": {"features": [{"name": f.get("name"), "stage": f.get("stage"), "enabled": f.get("enabled"),
                                           "description": f.get("description") or f.get("displayName") or ""}
                                          for f in res.get("data") or [] if f.get("stage") != "removed"]}}
        if name == "/goal":
            if sub == "clear":
                await self._call("thread/goal/clear", {"threadId": tid})
                return {"message": "Goal cleared."}
            if sub in ("pause", "resume"):
                await self._call("thread/goal/set", {"threadId": tid, "status": "paused" if sub == "pause" else "active"})
                return {"message": "Goal paused." if sub == "pause" else "Goal resumed."}
            if value:
                await self._call("thread/goal/set", {"threadId": tid, "objective": value, "status": "active"})
                return {"message": f"Goal: {value}"}
            res = await self._call("thread/goal/get", {"threadId": tid})
            return {"message": "" if res.get("goal") else "No goal set.", "data": res.get("goal")}

        if name == "/fork":
            res = await self._call("thread/fork", {"threadId": tid})
            new_id = (res.get("thread") or {}).get("id")
            self._spawn(self.resume_conversation(new_id))
            return {"message": "Forked a new conversation (the original is still available from 🕘)."}
        if name == "/rename":
            if not value:
                return {"ok": False, "message": "Usage: /rename <name>"}
            await self._call("thread/name/set", {"threadId": tid, "name": value})
            return {"message": f"Conversation renamed to \"{value}\"."}
        if name in ("/archive", "/delete"):
            await self._call("thread/archive" if name == "/archive" else "thread/delete", {"threadId": tid})
            self._spawn(self.new_conversation())
            return {"message": "Archived; started a new conversation." if name == "/archive" else "Permanently deleted; started a new conversation."}
        if name == "/compact":
            await self._call("thread/compact/start", {"threadId": tid})
            return {"message": "Compacting the conversation…"}
        if name == "/review":
            if sub == "branch" and rest:
                target = {"type": "baseBranch", "branch": rest}
            elif sub == "commit" and rest:
                target = {"type": "commit", "sha": rest}
            elif value:
                target = {"type": "custom", "instructions": value}
            else:
                target = {"type": "uncommittedChanges"}
            res = await self._call("review/start", {"threadId": tid, "target": target})
            self._turn_id = (res.get("turn") or {}).get("id") or self._turn_id
            return {"message": "Starting review…"}
        if name == "/diff":
            if not cwd:
                raise CommandUnsupported()
            # git, read-only, run by OopsBox: codex's command/exec would put
            # it in the sandbox, which some hosts cannot create
            return await asyncio.to_thread(_git_diff, cwd)
        if name == "/ps":
            res = await self._call("thread/backgroundTerminals/list", {"threadId": tid})
            return {"data": {"terminals": res.get("data") or []}}
        if name == "/stop":
            await self._call("thread/backgroundTerminals/clean", {"threadId": tid})
            return {"message": "Stopped all background terminals."}
        if name == "/rollout":
            res = await self._call("thread/read", {"threadId": tid})
            path = (res.get("thread") or {}).get("path")
            return {"message": path or "No log file yet."}
        if name == "/logout":
            await self._call("account/logout")
            return {"message": "Logged out of codex. To log in again, click \"Open in Terminal\" and run codex login.", "handoff": True}
        raise CommandUnsupported()

    async def _local_command(self, name: str, value: str) -> dict:
        if name in ("/new", "/clear"):
            await self.new_conversation()
            return {"message": "Started a new conversation."}
        if name == "/resume":
            return {"data": {"open": "conversations"}}
        if name == "/init":
            await self.prompt(INIT_PROMPT)
            return {"message": "Asked codex to create AGENTS.md."}
        if name == "/mention":
            return {"message": "Type @ and a file name in the input box to search for and mention a file."}
        if name == "/copy":
            last = next((b for b in reversed(list(self.blocks.values())) if b["kind"] == "assistant"), None)
            return {"message": "Copied the last reply." if last else "No reply to copy yet.",
                    "data": {"copy": last["text"]} if last else None}
        if name in ("/quit", "/exit"):
            self._spawn(self.stop())
            return {"message": "codex stopped."}
        return await super()._local_command(name, value)

    # ── Other conversations ───────────────────────────
    async def list_conversations(self) -> list[dict]:
        if self.project.is_ssh:
            return []
        if not self._rpc:
            await self.start()  # listing goes through the app-server
        res = await self._call("thread/list", {"cwd": str(self.project.path), "limit": 50})
        return [{"id": t.get("id"), "title": t.get("name") or t.get("preview") or "",
                 "updated": _epoch(t.get("updatedAt") or t.get("createdAt")),
                 "current": t.get("id") == self._thread_id}
                for t in res.get("data") or []]

    replays_history = True
    _show_history = False

    def _set_conversation_id(self, conversation_id: str) -> None:
        self.saved["thread_id"] = conversation_id
        self._show_history = True

    async def _render_history(self) -> None:
        """Draw a resumed thread's earlier turns, which the chat has not seen."""
        try:
            res = await self._call("thread/read", {"threadId": self._thread_id, "includeTurns": True}, timeout=60)
        except (RpcError, CommandUnsupported):
            return
        for turn in (res.get("thread") or {}).get("turns") or []:
            for item in turn.get("items") or []:
                if item.get("type") == "userMessage":
                    text = "\n".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "text")
                    if text:
                        self.upsert({"id": item.get("id") or "u-" + uuid.uuid4().hex[:12], "kind": "user", "text": text})
                else:
                    self._item(item, done=True)

    def terminal_command(self) -> Optional[list[str]]:
        tid = self.saved.get("thread_id")
        # a thread that never had a turn is not on disk, and `codex resume`
        # fails on it: there is nothing to continue, so start codex fresh
        on_disk = tid and (self.saved.get("on_disk") == tid or (
            # threads from before OopsBox kept track: look for the rollout
            not self.project.is_ssh and any((Path.home() / ".codex" / "sessions").glob(f"*/*/*/rollout-*-{tid}.jsonl"))))
        return ["codex", "resume", tid] if on_disk else ["codex"]

    # ── Agent → us: notifications ─────────────────────
    async def _on_notification(self, method: str, p: dict) -> None:
        if p.get("threadId") not in (None, self._thread_id):
            return  # sub-agent threads are summarised by their parent's items
        if method == "turn/started":
            self._turn_id = p.get("turn", {}).get("id")
            if self._thread_id and self.saved.get("on_disk") != self._thread_id:
                # codex writes a thread out with its first turn; from now on
                # `codex resume` can find it
                self.save_state(on_disk=self._thread_id)
            if self.status != "working":
                self.set_status("working")
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            if turn.get("status") == "failed" and turn.get("error"):
                self.notice(turn["error"].get("message", "turn failed"), "error")
            elif turn.get("status") == "interrupted":
                self.notice("Interrupted.")
            self._turn_id = None
            self.set_status("idle")
        elif method in ("item/started", "item/completed"):
            self._item(p.get("item") or {}, done=method == "item/completed")
        elif method == "item/agentMessage/delta":
            self.append_text(p["itemId"], "text", p.get("delta", ""))
        elif method in ("item/reasoning/summaryTextDelta", "item/reasoning/textDelta"):
            self.append_text(p["itemId"], "text", p.get("delta", ""), kind="thought")
        elif method == "item/commandExecution/outputDelta":
            block = self.blocks.get(p["itemId"])
            if block is not None and len(block.get("output") or "") < 20_000:
                self.append_text(p["itemId"], "output", p.get("delta", ""), kind="tool")
        elif method == "error":
            err = p.get("error") or {}
            retry = " (retrying)" if p.get("willRetry") else ""
            self.notice(f"{err.get('message', 'error')}{retry}", "warn" if retry else "error")
        elif method == "thread/compacted":
            self.notice("Conversation compacted.")
        elif method == "thread/tokenUsage/updated":
            usage = p.get("tokenUsage") or {}
            window = usage.get("modelContextWindow")
            used = (usage.get("last") or {}).get("totalTokens")
            if window and used is not None:
                self.set_usage(100 * used / window, f"{used // 1000}k / {window // 1000}k tokens")
        elif method == "turn/plan/updated":
            marks = {"completed": "☑", "inProgress": "▶", "pending": "☐"}
            lines = [f"{marks.get(s.get('status'), '•')} {s.get('step', '')}" for s in p.get("plan") or []]
            if p.get("explanation"):
                lines.insert(0, p["explanation"])
            self.upsert({"id": f"plan-{p.get('turnId')}", "kind": "plan", "text": "\n".join(lines)})

    def _item(self, item: dict, done: bool) -> None:
        kind = item.get("type")
        iid = item.get("id")
        if not iid or kind == "userMessage":
            return  # we record the user's message ourselves when it is sent
        if kind == "agentMessage":
            self.upsert({"id": iid, "kind": "assistant", "text": item.get("text") or
                         self.blocks.get(iid, {}).get("text", "")}, persist=done)
        elif kind == "reasoning":
            text = "\n\n".join(item.get("summary") or []) or "\n\n".join(item.get("content") or [])
            text = text or self.blocks.get(iid, {}).get("text", "")
            if text:
                self.upsert({"id": iid, "kind": "thought", "text": text}, persist=done)
        elif kind == "commandExecution":
            status = _STATUS.get(item.get("status"), "running")
            if status == "completed" and item.get("exitCode") not in (0, None):
                status = "failed"
            block = {
                "id": iid, "kind": "tool", "tool": "execute",
                "title": _command_text(item.get("command")),
                "status": status,
            }
            if done:
                block["output"] = clip(item.get("aggregatedOutput"))
                block["exit_code"] = item.get("exitCode")
            self.upsert(block, persist=done)
        elif kind == "fileChange":
            changes = item.get("changes") or []
            paths = [c.get("path", "") for c in changes]
            self.upsert({
                "id": iid, "kind": "tool", "tool": "edit",
                "title": "Edit " + ", ".join(self._rel(p) for p in paths),
                "status": _STATUS.get(item.get("status"), "running"),
                "detail": clip("\n".join(
                    f"--- {self._rel(c.get('path', ''))} ({c.get('kind', {}).get('type', '')})\n{c.get('diff', '')}"
                    for c in changes)),
            }, persist=done)
        elif kind == "mcpToolCall":
            self.upsert({
                "id": iid, "kind": "tool", "tool": "other",
                "title": f"{item.get('server')}.{item.get('tool')}",
                "status": _STATUS.get(item.get("status"), "running"),
                "detail": clip(json.dumps(item.get("arguments"), ensure_ascii=False, indent=2)),
                "output": clip(json.dumps(item.get("result") or item.get("error"), ensure_ascii=False, indent=2))
                if done else "",
            }, persist=done)
        elif kind == "webSearch":
            self.upsert({
                "id": iid, "kind": "tool", "tool": "search",
                "title": "Search: " + (item.get("query") or ""),
                "status": "completed" if done else "running",
            }, persist=done)
        elif kind == "plan":
            self.upsert({"id": iid, "kind": "plan", "text": item.get("text", "")}, persist=done)
        elif kind == "contextCompaction":
            if done:
                self.notice("Conversation compacted.")
        elif done and kind not in ("hookPrompt", "sleep"):
            self.upsert({
                "id": iid, "kind": "tool", "tool": "other", "title": kind or "item",
                "status": _STATUS.get(item.get("status"), "completed"),
            })

    def _rel(self, path: str) -> str:
        root = str(self.project.path) + "/"
        return path[len(root):] if path.startswith(root) else path

    # ── Agent → us: requests ──────────────────────────
    async def _elicit(self, p: dict) -> dict:
        """An MCP server asks the user for input: a form, or a page to visit."""
        server = p.get("serverName", "MCP")
        bid = "q-" + uuid.uuid4().hex[:12]
        if p.get("mode") == "url":
            answers = await self.ask_question(bid, [{
                "id": "done", "header": server, "question": f"{p.get('message', '')}\n{p.get('url', '')}",
                "options": [{"label": "Done", "description": ""}, {"label": "Cancel", "description": ""}],
                "multi": False, "other": False}])
            ok = (answers or {}).get("done") == ["Done"]
            return {"action": "accept" if ok else "decline"}
        schema = p.get("requestedSchema") or {}
        props = schema.get("properties") or {}
        questions = []
        for key, prop in props.items():
            opts = []
            if prop.get("type") == "boolean":
                opts = ["true", "false"]
            for o in prop.get("oneOf") or prop.get("anyOf") or []:
                if isinstance(o, dict) and "const" in o:
                    opts.append(str(o["const"]))
            opts += [str(x) for x in prop.get("enum") or []]
            questions.append({
                "id": key, "header": server if not questions else "",
                "question": prop.get("title") or prop.get("description") or key,
                "options": [{"label": o, "description": ""} for o in opts],
                "multi": prop.get("type") == "array", "other": not opts,
            })
        if not questions:
            questions = [{"id": "_ok", "header": server, "question": p.get("message", ""),
                          "options": [{"label": "OK", "description": ""}], "multi": False, "other": False}]
        elif p.get("message"):
            questions[0]["question"] = f"{p['message']}\n\n{questions[0]['question']}"
        answers = await self.ask_question(bid, questions)
        if not answers:
            return {"action": "cancel"}
        content = {}
        for key, prop in props.items():
            vals = answers.get(key) or []
            if not vals:
                continue
            t = prop.get("type")
            if t == "array":
                content[key] = vals
            elif t == "boolean":
                content[key] = vals[0] == "true"
            elif t in ("number", "integer"):
                try:
                    content[key] = int(vals[0]) if t == "integer" else float(vals[0])
                except ValueError:
                    content[key] = vals[0]
            else:
                content[key] = vals[0]
        missing = [k for k in schema.get("required") or [] if k not in content]
        return {"action": "decline"} if missing else {"action": "accept", "content": content}

    async def _on_request(self, method: str, p: dict) -> Any:
        if method == "item/commandExecution/requestApproval":
            cmd = _command_text(p.get("command"))
            detail = cmd + (f"\n\nReason: {p['reason']}" if p.get("reason") else "")
            choice = await self.ask_permission(
                "perm-" + str(p.get("approvalId") or p.get("itemId")), "Run command", detail, _ALLOW)
            return {"decision": choice or "cancel"}
        if method == "item/fileChange/requestApproval":
            block = self.blocks.get(p.get("itemId"), {})
            detail = (block.get("title") or "Edit files") + "\n\n" + (block.get("detail") or "")
            if p.get("reason"):
                detail += f"\n\nReason: {p['reason']}"
            choice = await self.ask_permission("perm-" + p.get("itemId", ""), "Edit files", detail, _ALLOW)
            return {"decision": choice or "cancel"}
        if method == "item/permissions/requestApproval":
            detail = json.dumps(p.get("permissions"), ensure_ascii=False, indent=2)
            if p.get("reason"):
                detail = f"{p['reason']}\n\n{detail}"
            choice = await self.ask_permission("perm-" + p.get("itemId", ""), "Request additional permissions", detail, [
                PermissionOption("turn", "Allow for this turn", "allow_once"),
                PermissionOption("session", "Allow for this conversation", "allow_always"),
                PermissionOption("deny", "Deny", "reject"),
            ])
            if choice in ("turn", "session"):
                return {"permissions": p.get("permissions") or {}, "scope": choice}
            return {"permissions": {}}
        if method == "mcpServer/elicitation/request":
            return await self._elicit(p)
        if method == "item/tool/requestUserInput":
            questions = [{
                "id": q.get("id"), "header": q.get("header", ""), "question": q.get("question", ""),
                "options": [{"label": o.get("label", ""), "description": o.get("description", "")}
                            for o in q.get("options") or []],
                "multi": False, "other": bool(q.get("isOther")) or not q.get("options"),
                "secret": bool(q.get("isSecret")),
            } for q in p.get("questions") or []]
            answers = await self.ask_question("q-" + p.get("itemId", uuid.uuid4().hex), questions)
            return {"answers": {qid: {"answers": vals} for qid, vals in (answers or {}).items()}}
        raise RpcError({"code": -32601, "message": f"OopsBox does not handle {method}"})


def _epoch(value) -> float:
    """codex reports times as epoch seconds or as ISO strings."""
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 1e12 else float(value)
    if isinstance(value, str):
        from datetime import datetime
        try:
            return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _git_diff(cwd: str) -> dict:
    import subprocess
    has_head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=cwd,
                              capture_output=True, timeout=30).returncode == 0
    # before the first commit there is no HEAD; staged and unstaged is everything
    diff = subprocess.run(["git", "diff", "HEAD"] if has_head else ["git", "diff"], cwd=cwd,
                          capture_output=True, text=True, timeout=30)
    if diff.returncode != 0:
        return {"ok": False, "message": (diff.stderr or "git diff failed").strip()[:300]}
    new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=cwd,
                         capture_output=True, text=True, timeout=30).stdout.split()
    text = diff.stdout.strip()
    if not text and not new:
        return {"message": "No changes."}
    return {"data": {"diff": clip(text, 100_000), "untracked": new}}

