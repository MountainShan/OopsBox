# dashboard/agents/catalog.py
"""The agents OopsBox knows, and which of them a machine has installed.

Detection runs `command -v` and the agent's version flag on each machine
projects live on: this one, and every SSH host a project points at. It uses
the PATH the agents are started with, so "installed" means OopsBox can start
it. The result is a hint for choosing a project's agents, never a gate:
a host can be unreachable for a while, or an agent installed since.
"""
import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .launch import build_command
from .session import ProjectInfo

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    binary: str
    install_hint: str
    version_args: tuple[str, ...] = ("--version",)
    stability: str = "stable"  # or "experimental": not yet checked to match its CLI
    # for an agent driven by the generic ACP driver: how to start it in ACP mode
    acp_command: tuple[str, ...] = ()


CATALOG: tuple[AgentSpec, ...] = (
    AgentSpec("codex", "Codex", "codex",
              "npm install -g @openai/codex, then sign in with `codex`"),
    AgentSpec("claude", "Claude Code", "claude",
              "npm install -g @anthropic-ai/claude-code, then sign in with `claude`"),
    AgentSpec("kiro", "Kiro", "kiro-cli",
              "Install the Kiro CLI (kiro-cli) from kiro.dev, then sign in to it"),
    # Experimental: they speak ACP, so the generic driver runs them, but
    # nobody has yet checked that OopsBox gives you what their own CLI does.
    # The ACP commands are the ones their documentation gives.
    AgentSpec("gemini", "Gemini CLI", "gemini",
              "npm install -g @google/gemini-cli, then sign in with `gemini`",
              stability="experimental", acp_command=("gemini", "--acp")),
    AgentSpec("goose", "Goose", "goose",
              "Install goose from goose-docs.ai, then set it up with `goose configure`",
              stability="experimental", acp_command=("goose", "acp")),
    AgentSpec("opencode", "OpenCode", "opencode",
              "Install OpenCode from opencode.ai, then sign in to it",
              stability="experimental", acp_command=("opencode", "acp")),
)
STABLE = [s.id for s in CATALOG if s.stability == "stable"]
SPECS = {s.id: s for s in CATALOG}

LOCAL = "local"
TIMEOUT = 15  # seconds for one machine, all agents
_cache_file = Path.home() / ".config" / "oopsbox" / "agents-detected.json"


def host_key(meta: Optional[dict]) -> str:
    """The machine a project's agents run on: "local", or user@host:port."""
    if not meta or meta.get("type") != "ssh":
        return LOCAL
    return f"{meta.get('ssh_user') or ''}@{meta.get('ssh_host') or ''}:{meta.get('ssh_port') or 22}"


def _script() -> str:
    # one line per agent: id <tab> path <tab> first line of its version
    lines = []
    for s in CATALOG:
        version = " ".join([s.binary, *s.version_args])
        lines.append(
            f'p=$(command -v {s.binary} 2>/dev/null); '
            # the first line that looks like a version (some print warnings first)
            f'if [ -n "$p" ]; then v=$(timeout 8 {version} 2>&1 | grep -m 1 -E "[0-9]+\\.[0-9]+"); else v=; fi; '
            f'printf "%s\\t%s\\t%s\\n" {s.id} "$p" "$v"')
    return "\n".join(lines)


def parse(out: str) -> dict[str, dict]:
    found = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3 or parts[0] not in SPECS:
            continue
        agent, path, version = parts
        found[agent] = ({"state": "installed", "path": path, "version": version.strip()[:120]}
                        if path else {"state": "missing"})
    # an agent the script never got to (cut short) is not known either way
    return {a: found.get(a, {"state": "unknown"}) for a in SPECS}


async def probe(meta: Optional[dict]) -> dict:
    """Detect the agents on the machine this project (None: this one) uses."""
    if meta is None or meta.get("type") != "ssh":
        meta = {"name": "_detect", "type": "local", "path": str(Path.home())}
    argv, cwd, env = build_command(ProjectInfo.from_registry(meta), ["bash", "-c", _script()])
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd if cwd and Path(cwd).is_dir() else None, env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
    except (OSError, ValueError) as e:
        return {"at": time.time(), "error": str(e), "agents": parse("")}
    try:
        out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"at": time.time(), "error": "timed out", "agents": parse("")}
    result = {"at": time.time(), "agents": parse(out.decode(errors="replace"))}
    if proc.returncode and all(a["state"] == "unknown" for a in result["agents"].values()):
        result["error"] = (err.decode(errors="replace").strip().splitlines() or ["could not connect"])[-1][:200]
    return result


def load() -> dict[str, dict]:
    try:
        return json.loads(_cache_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    _cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = _cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_cache_file)


def hosts(registry: dict) -> dict[str, Optional[dict]]:
    """{host key: a project's meta to reach it by (None for this machine)}."""
    out: dict[str, Optional[dict]] = {LOCAL: None}
    for meta in registry.values():
        out.setdefault(host_key(meta), meta if meta.get("type") == "ssh" else None)
    return out


async def detect(registry: dict, only: Optional[str] = None) -> dict[str, dict]:
    """Detect on every machine projects use (or just `only`) and remember it."""
    known = hosts(registry)
    targets = {k: m for k, m in known.items() if only is None or k == only}

    async def one(key, meta):
        try:
            r = await probe(meta)
        except Exception as e:
            log.warning("agent detection on %s failed: %s", key, e)
            r = {"at": time.time(), "error": str(e), "agents": parse("")}
        # each machine is saved as it answers: a slow one holds up no other
        # (no await between load and save, so two cannot interleave)
        data = load()
        data[key] = r
        _save({k: v for k, v in data.items() if k in known})  # and forget machines no project uses

    await asyncio.gather(*(one(k, m) for k, m in targets.items()))
    data = {k: v for k, v in load().items() if k in known}
    _save(data)
    return data


def catalog() -> list[dict]:
    return [asdict(s) for s in CATALOG]


def installed(registry_meta: Optional[dict], agent: str) -> Optional[bool]:
    """True / False when detection knows, None when it does not."""
    state = load().get(host_key(registry_meta), {}).get("agents", {}).get(agent, {}).get("state")
    return {"installed": True, "missing": False}.get(state)
