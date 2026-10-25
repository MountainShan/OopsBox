# dashboard/memory.py
"""Per-project memory store.

Lives at <project>/.memory, excluded from the project repo via .gitignore
and versioned as its own git repo, so it does not diverge across branches
or worktrees (spec §7, §8.2).

Everything here is plain markdown — no tool-specific format — so any agent
can read it (spec §8.3).
"""
import subprocess
from pathlib import Path

MEMORY_DIRNAME = ".memory"
SUBDIRS = ("knowledge", "decisions", "journal")
STATUS_FILENAME = "STATUS.md"
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
