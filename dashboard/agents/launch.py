# dashboard/agents/launch.py
"""Where an agent process runs, and what it is told about its project.

Every agent runs on the OopsBox host, where it is installed and signed in.
A local project runs it in the project directory. An SSH project runs it in
the remote folder mounted here over sshfs (agents/remote.py), with a
`remote` command on its PATH for running commands on the remote machine.
"""
import os
import re
from pathlib import Path
from typing import Optional

from ..memory import MEMORY_DIRNAME, STATUS_FILENAME, memory_root
from .session import ProjectInfo

_SSH_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*")


def ssh_target(user: Optional[str], host: Optional[str], port, key_path: Optional[str]) -> tuple[str, int, Optional[str]]:
    """Checked SSH connection fields: none may pass for an ssh option
    ("-oProxyCommand=…" as a user or host would run a command)."""
    if not user or not host or not _SSH_NAME.fullmatch(user) or not _SSH_NAME.fullmatch(host):
        raise ValueError("Invalid SSH user or host name")
    try:
        port = int(port or 22)
    except (TypeError, ValueError):
        raise ValueError("Invalid SSH port")
    if not 0 < port < 65536:
        raise ValueError("Invalid SSH port")
    if key_path and key_path.startswith("-"):
        raise ValueError("Invalid SSH key path")
    return f"{user}@{host}", port, key_path


def build_command(project: ProjectInfo, argv: list[str],
                  extra_env: Optional[dict[str, str]] = None) -> tuple[list[str], Optional[str], dict]:
    """Return (argv, cwd, env) to spawn `argv` for this project.

    An SSH project's folder must be mounted first (remote.ensure()); the
    session does that before it starts the agent.
    """
    env = dict(os.environ)
    env.update(extra_env or {})
    # The dashboard runs as a service whose PATH lacks the per-user bin
    # directories that kiro-cli and npm-installed agents live in.
    home = str(Path.home())
    prefix = f"{home}/.local/bin:{home}/.npm-global/bin:"
    if project.is_ssh:
        prefix = f"{project.remote_bin}:" + prefix  # `remote <command>`
    env["PATH"] = prefix + env.get("PATH", "/usr/bin:/bin")
    return argv, str(project.workdir), env


def _memory_text(store: str, where: str) -> str:
    return f"""\
## Shared project memory (OopsBox)

This project keeps a memory store in `{store}/` {where}.
Every agent that works on this project (Claude, Codex, Kiro) reads and writes
the same files, so it is how you pick up where another agent left off.

- Before you start work, read `{store}/{STATUS_FILENAME}` and skim the
  file names in `{store}/decisions/`; open any that bear on the task.
- When you finish a piece of work, update `{store}/{STATUS_FILENAME}`:
  what is done (Now), what comes next (Next), what is stuck (Blocked).
- When you make a decision someone would otherwise have to rediscover, add
  `{store}/decisions/YYYY-MM-DD-<short-slug>.md` saying what was decided
  and why. Durable facts about the project go in `{store}/knowledge/`.
- Plain markdown only. `{store}/` is its own git repo and is ignored by
  the project repo — do not add it to project commits.
"""


_LOCAL_MEMORY = _memory_text(MEMORY_DIRNAME, "at the project root")


def _extra_dirs_text(project: ProjectInfo) -> str:
    if not project.extra_dirs:
        return ""
    listed = "\n".join(f"- `{d}`" for d in project.extra_dirs)
    return ("\n## Other folders (OopsBox)\n\n"
            "Besides your working directory, this project uses these folders. You may read "
            "and change files in them when the task calls for it:\n\n" + listed + "\n")


def memory_instructions(project: ProjectInfo) -> str:
    """What an agent is told about its project: the shared memory, the
    remote machine (SSH projects) and the extra folders."""
    if not project.is_ssh:
        return _LOCAL_MEMORY + _extra_dirs_text(project)
    from .remote import instructions as remote_instructions
    # the store is on this host, in the project's local folder, not on the remote
    memory = _memory_text(str(memory_root(project.path)), "on this host (not in the remote folder)")
    return remote_instructions(project) + "\n" + memory + _extra_dirs_text(project)
