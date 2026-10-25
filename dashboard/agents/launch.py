# dashboard/agents/launch.py
"""Where an agent process runs, and what it is told about shared memory.

A local project runs the agent in the project directory. An SSH project runs
the agent *on the remote host* over `ssh -T`: the agent's stdio is the JSON
stream, so the protocol works unchanged across the hop, and every file read,
edit and command happens on the remote machine. The agent CLI therefore has
to be installed and logged in on that host.
"""
import os
import re
import shlex
from pathlib import Path
from typing import Optional

from ..memory import MEMORY_DIRNAME, STATUS_FILENAME, list_files, memory_root
from .session import ProjectInfo

# Remote shells are often non-login, which skips the PATH additions that
# put ~/.local/bin and npm globals on PATH. Running through `bash -lc` picks
# them up the same way an interactive login would.
_REMOTE_PATH_PREFIX = 'export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"; '


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

    extra_env reaches the agent wherever it runs: through the environment
    locally, through `export` in the remote shell over SSH.
    """
    env = dict(os.environ)
    extra_env = extra_env or {}
    if not project.is_ssh:
        env.update(extra_env)
        # The dashboard runs as a service whose PATH lacks the per-user bin
        # directories that kiro-cli and npm-installed agents live in.
        home = str(Path.home())
        env["PATH"] = f"{home}/.local/bin:{home}/.npm-global/bin:" + env.get("PATH", "/usr/bin:/bin")
        return argv, str(project.path), env

    remote_dir = project.remote_path or "~"
    # A quoted `~` is not expanded, so home-relative paths go through $HOME.
    if remote_dir == "~":
        cd_target = '"$HOME"'
    elif remote_dir.startswith("~/"):
        cd_target = '"$HOME"/' + shlex.quote(remote_dir[2:])
    else:
        cd_target = shlex.quote(remote_dir)
    exports = "".join(f"export {k}={shlex.quote(v)}; " for k, v in extra_env.items())
    remote = f"{_REMOTE_PATH_PREFIX}{exports}cd {cd_target} && exec {shlex.join(argv)}"
    dest, port, key = ssh_target(project.ssh_user, project.ssh_host, project.ssh_port, project.ssh_key_path)
    ssh = [
        "ssh", "-T",
        "-p", str(port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ServerAliveInterval=30",
    ]
    if key:
        ssh += ["-i", key]
    if not project.ssh_password:
        ssh += ["-o", "BatchMode=yes"]
    ssh += ["--", dest, "bash -lc " + shlex.quote(remote)]
    if project.ssh_password:
        # -e reads the password from the environment, so it never appears in
        # the process list the way `sshpass -p` would put it.
        env["SSHPASS"] = project.ssh_password
        ssh = ["sshpass", "-e"] + ssh
    return ssh, str(project.path), env


_LOCAL_MEMORY = f"""\
## Shared project memory (OopsBox)

This project keeps a memory store in `{MEMORY_DIRNAME}/` at the project root.
Every agent that works on this project (Claude, Codex, Kiro) reads and writes
the same files, so it is how you pick up where another agent left off.

- Before you start work, read `{MEMORY_DIRNAME}/{STATUS_FILENAME}` and skim the
  file names in `{MEMORY_DIRNAME}/decisions/`; open any that bear on the task.
- When you finish a piece of work, update `{MEMORY_DIRNAME}/{STATUS_FILENAME}`:
  what is done (Now), what comes next (Next), what is stuck (Blocked).
- When you make a decision someone would otherwise have to rediscover, add
  `{MEMORY_DIRNAME}/decisions/YYYY-MM-DD-<short-slug>.md` saying what was decided
  and why. Durable facts about the project go in `{MEMORY_DIRNAME}/knowledge/`.
- Plain markdown only. `{MEMORY_DIRNAME}/` is its own git repo and is ignored by
  the project repo — do not add it to project commits.
"""

# Remote agents cannot reach the store, which lives on the OopsBox host, so
# they are handed a snapshot instead.
_SNAPSHOT_LIMIT = 12_000


def memory_instructions(project: ProjectInfo) -> str:
    if not project.is_ssh:
        return _LOCAL_MEMORY
    root = memory_root(project.path)
    parts = [
        "## Shared project memory (OopsBox, read-only snapshot)\n",
        "Other agents on this project keep notes on the OopsBox host. You are on the "
        "remote machine and cannot edit them; below is their current content. "
        "When you finish work or make a decision worth remembering, say so plainly "
        "at the end of your reply so it can be recorded.\n",
    ]
    budget = _SNAPSHOT_LIMIT
    names = [STATUS_FILENAME] + [n for n in list_files(project.path)
                                 if n.startswith("decisions/") and n != STATUS_FILENAME]
    for name in names:
        path: Path = root / name
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if not text:
            continue
        chunk = f"\n### {name}\n\n{text}\n"
        if len(chunk) > budget:
            parts.append("\n(… more memory files omitted)\n")
            break
        parts.append(chunk)
        budget -= len(chunk)
    return "".join(parts)
