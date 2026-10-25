# dashboard/agents/manager.py
"""Owns every chat session in the dashboard process.

Agent processes are children of the dashboard: they start on the first
message (or an explicit start) and stop with the project, on delete, and at
shutdown. Conversations outlive them — they are on disk, and each driver
resumes its agent's own session id when the agent starts again. An agent
that was running when the dashboard stopped (rather than stopped by
someone) is started again when it comes back: restore().
"""
import asyncio
import shutil
from pathlib import Path
from typing import Callable, Optional

from ..memory import ensure_memory
from .acp import KiroSession
from .claude import ClaudeSession
from .codex import CodexSession
from .launch import memory_instructions
from .session import AgentSession, ChatStore, ProjectInfo

DRIVERS: dict[str, type[AgentSession]] = {
    "codex": CodexSession,
    "claude": ClaudeSession,
    "kiro": KiroSession,
}
AGENT_ORDER = ["codex", "claude", "kiro"]

CHAT_ROOT = Path.home() / ".config" / "oopsbox" / "chats"


class SessionManager:
    def __init__(self, root: Path = CHAT_ROOT):
        self.root = root
        self._sessions: dict[tuple[str, str], AgentSession] = {}
        # (project, agent, kind, text) when an agent waits for the user;
        # main.py points it at notify.attention
        self.attention_hook: Optional[Callable[[str, str, str, str], None]] = None

    def _project_dir(self, project: str) -> Path:
        return self.root / project

    def get(self, meta: dict, agent: str) -> AgentSession:
        project = ProjectInfo.from_registry(meta)
        key = (project.name, agent)
        session = self._sessions.get(key)
        if session is None:
            store = ChatStore(self._project_dir(project.name) / agent)
            session = DRIVERS[agent](project, store)
            session.on_attention = lambda kind, text, p=project.name, a=agent: \
                self.attention_hook and self.attention_hook(p, a, kind, text)
            self._sessions[key] = session
        else:
            # connection settings may have been edited since the session was made
            session.project = project
        if project.path.is_dir():
            ensure_memory(project.path)
        # Rebuilt on every lookup so a remote agent starting now gets the
        # memory as it is now, not as it was when the dashboard started.
        session.instructions = memory_instructions(project)
        return session

    def peek(self, project: str, agent: str) -> Optional[AgentSession]:
        return self._sessions.get((project, agent))

    def running(self, project: str) -> list[str]:
        return [a for a in AGENT_ORDER
                if (s := self._sessions.get((project, a))) is not None and s.alive]

    def any_alive(self, agent: str) -> Optional[AgentSession]:
        """A running session of this agent, in any project (for questions
        about the agent's account rather than a conversation)."""
        return next((s for (_, a), s in self._sessions.items() if a == agent and s.alive), None)

    def projects_running(self, agent: str) -> list[str]:
        return sorted(p for (p, a), s in self._sessions.items() if a == agent and s.alive)

    def summary(self, project: str) -> dict[str, dict]:
        """{agent: {status, waiting}} for the agents running in a project."""
        return {a: {"status": s.status, "waiting": s.waiting} for a in AGENT_ORDER
                if (s := self._sessions.get((project, a))) is not None and s.alive}

    def waiting(self) -> list[dict]:
        """Every agent, in any project, that waits for the user."""
        return [{"project": p, "agent": a} for (p, a), s in sorted(self._sessions.items())
                if s.alive and s.waiting]

    def status(self, project: str, agent: str) -> str:
        s = self._sessions.get((project, agent))
        return s.status if s else "stopped"

    def last_agent(self, project: str) -> Optional[str]:
        try:
            name = (self._project_dir(project) / "last_agent").read_text().strip()
        except OSError:
            return None
        return name if name in DRIVERS else None

    def default_agent(self, project: str, access: dict[str, str]) -> Optional[str]:
        """The agent a project opens on: the last one used if still allowed,
        else the first available in AGENT_ORDER, else the first hidden one."""
        last = self.last_agent(project)
        if last and access.get(last, "blocked") != "blocked":
            return last
        for state in ("available", "hidden"):
            for a in AGENT_ORDER:
                if access.get(a) == state:
                    return a
        return None

    def set_last_agent(self, project: str, agent: str) -> None:
        d = self._project_dir(project)
        d.mkdir(parents=True, exist_ok=True)
        (d / "last_agent").write_text(agent)

    async def stop_project(self, project: str) -> None:
        await asyncio.gather(*(s.stop() for (p, _), s in list(self._sessions.items())
                               if p == project and s.alive))

    async def drop_project(self, project: str) -> None:
        """Stop a project's agents and let go of them; its chats stay on disk."""
        await self.stop_project(project)
        for key in [k for k in self._sessions if k[0] == project]:
            del self._sessions[key]

    async def forget_project(self, project: str) -> None:
        await self.drop_project(project)
        shutil.rmtree(self._project_dir(project), ignore_errors=True)

    async def shutdown(self) -> None:
        await asyncio.gather(*(s.stop(keep_standby=True) for s in self._sessions.values() if s.alive),
                             return_exceptions=True)

    async def restore(self, meta: dict, allowed: list[str]) -> list[str]:
        """Start the project's agents that were on standby when the dashboard
        stopped, among those still allowed. Returns the ones started."""
        project = meta["name"]
        wanted = []
        for agent in AGENT_ORDER:
            if agent not in allowed:
                continue
            state = ChatStore(self._project_dir(project) / agent).load_state()
            if state.get("standby") and not state.get("handed_off"):
                wanted.append(self.get(meta, agent))
        await asyncio.gather(*(s.start() for s in wanted if not s.alive), return_exceptions=True)
        return [s.agent for s in wanted if s.alive]


manager = SessionManager()
