# dashboard/memory.py
"""Per-project memory store.

Lives at <project>/.memory, excluded from the project repo via .gitignore
and versioned as its own git repo, so it does not diverge across branches
or worktrees (spec §7, §8.2).

Everything here is plain markdown — no tool-specific format — so any agent
can read it (spec §8.3).
"""
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

MEMORY_DIRNAME = ".memory"
SUBDIRS = ("knowledge", "decisions", "journal")
STATUS_FILENAME = "STATUS.md"
ACTIVITY_FILENAME = "activity.md"
ACTIVITY_KEEP = 150  # entries; older ones are dropped
_GITIGNORE_ENTRY = ".memory/"

_STATUS_TEMPLATE = """# Status

## Now

## Next

## Blocked
"""


def memory_root(project_dir: Path) -> Path:
    return project_dir / MEMORY_DIRNAME


def ensure_memory(project_dir: Path) -> Path:
    """Create the memory store if absent. Idempotent.

    Safe on existing projects: only creates what is missing, and never
    rewrites an existing STATUS.md or .gitignore.
    """
    root = memory_root(project_dir)
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)

    status = root / STATUS_FILENAME
    if not status.exists():
        status.write_text(_STATUS_TEMPLATE, encoding="utf-8")

    _ensure_gitignored(project_dir)
    _ensure_own_repo(root)
    return root


def _ensure_gitignored(project_dir: Path) -> None:
    gitignore = project_dir / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if any(line.strip() == _GITIGNORE_ENTRY for line in existing.splitlines()):
        return
    separator = "" if existing == "" or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{separator}{_GITIGNORE_ENTRY}\n", encoding="utf-8")


def _ensure_own_repo(root: Path) -> None:
    if (root / ".git").exists():
        return
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


_ACTIVITY_HEAD = """# Activity

Written by OopsBox after every turn of every agent on this project: what it
was asked, what it changed and what it answered. Newest last. Agents: read
the last entries before you start; you do not need to write here.
"""


def _one_line(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def append_activity(project_dir: Path, agent: str, asked: str, answer: str,
                    changed: Optional[list[str]] = None, when: Optional[float] = None) -> None:
    """Record one agent turn in .memory/activity.md (keeping the last
    ACTIVITY_KEEP entries)."""
    root = memory_root(project_dir)
    if not root.is_dir():
        return
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(when or time.time()))
    lines = [f"### {stamp} · {agent}", f"- Asked: {_one_line(asked, 300) or '(nothing)'}"]
    if changed:
        shown = list(dict.fromkeys(changed))
        more = f" (+{len(shown) - 10} more)" if len(shown) > 10 else ""
        lines.append(f"- Changed: {', '.join(_one_line(c, 120) for c in shown[:10])}{more}")
    lines.append(f"- Answered: {_one_line(answer, 600) or '(no answer)'}")
    path = root / ACTIVITY_FILENAME
    try:
        old = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        old = ""
    entries = [e for e in re.split(r"\n(?=### )", old) if e.startswith("### ")]
    entries = (entries + ["\n".join(lines) + "\n"])[-ACTIVITY_KEEP:]
    path.write_text(_ACTIVITY_HEAD + "\n" + "\n".join(e.rstrip("\n") + "\n" for e in entries), encoding="utf-8")


def recent_activity(project_dir: Path, entries: int = 6, limit: int = 3000) -> str:
    """The last few activity entries, newest last, within `limit` characters."""
    try:
        text = (memory_root(project_dir) / ACTIVITY_FILENAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    found = [e.strip() for e in re.split(r"\n(?=### )", text) if e.startswith("### ")][-entries:]
    out = []
    for e in reversed(found):
        if sum(len(x) for x in out) + len(e) > limit:
            break
        out.insert(0, e)
    return "\n\n".join(out)


def list_files(project_dir: Path) -> list[str]:
    """Every markdown file under .memory, relative to it, sorted."""
    root = memory_root(project_dir)
    if not root.exists():
        return []
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.md")
        if ".git" not in path.parts
    )


def read_file(project_dir: Path, rel_path: str) -> str:
    """Read one file under .memory.

    Raises ValueError if the path escapes the store, FileNotFoundError if
    it does not exist.
    """
    root = memory_root(project_dir).resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Path escapes the memory store")
    if ".git" in target.relative_to(root).parts:
        raise FileNotFoundError(rel_path)
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target.read_text(encoding="utf-8")
