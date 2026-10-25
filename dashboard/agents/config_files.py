# dashboard/agents/config_files.py
"""The agents' own settings files, read and written in place.

Editing the file the CLI itself reads — rather than keeping OopsBox's own
copy of the settings — means a change made in the browser holds when the
agent is run in a terminal, and the other way round.
"""
import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ConfigFile:
    scope: str        # user | project | local | mcp | project-mcp
    path: Path
    format: str       # json | toml
    description: str

    def as_dict(self) -> dict:
        return {"scope": self.scope, "path": str(self.path), "format": self.format,
                "description": self.description, "exists": self.path.exists()}


def config_files(agent: str, project_dir: Path, home: Optional[Path] = None) -> list[ConfigFile]:
    home = home or Path.home()
    if agent == "claude":
        return [
            ConfigFile("user", home / ".claude/settings.json", "json", "Settings shared by all projects"),
            ConfigFile("project", project_dir / ".claude/settings.json", "json", "This project's settings (committed to git)"),
            ConfigFile("local", project_dir / ".claude/settings.local.json", "json", "This project's settings on this machine only"),
        ]
    if agent == "codex":
        return [
            ConfigFile("user", home / ".codex/config.toml", "toml", "Settings shared by all projects"),
            ConfigFile("project", project_dir / ".codex/config.toml", "toml", "This project's settings"),
        ]
    if agent == "kiro":
        return [
            ConfigFile("user", home / ".kiro/settings/cli.json", "json", "What kiro-cli settings holds"),
            ConfigFile("mcp", home / ".kiro/settings/mcp.json", "json", "MCP servers shared by all projects"),
            ConfigFile("project-mcp", project_dir / ".kiro/settings/mcp.json", "json", "This project's MCP servers"),
        ]
    return []


def find(agent: str, project_dir: Path, scope: str, home: Optional[Path] = None) -> ConfigFile:
    for f in config_files(agent, project_dir, home):
        if f.scope == scope:
            return f
    raise KeyError(scope)


def read_config(f: ConfigFile) -> str:
    try:
        return f.path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def validate(fmt: str, content: str) -> None:
    """Raise ValueError if content is not valid for the format."""
    if not content.strip():
        return
    try:
        if fmt == "json":
            json.loads(content)
        elif fmt == "toml":
            tomllib.loads(content)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"Not valid {fmt.upper()}: {e}")


def write_config(f: ConfigFile, content: str) -> None:
    """Validate, back up the old file, and replace it atomically.

    A symlinked settings file (shared from a dotfiles repo) is written
    through to its target, so the link survives.
    """
    validate(f.format, content)
    target = f.path.resolve() if f.path.is_symlink() else f.path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".oopsbox-bak"))
    tmp = target.with_name(target.name + ".oopsbox-tmp")
    tmp.write_text(content, encoding="utf-8")
    if target.exists():
        shutil.copymode(target, tmp)
    os.replace(tmp, target)
