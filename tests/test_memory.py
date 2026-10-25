import subprocess
import tempfile
import unittest
from pathlib import Path

from dashboard.memory import (
    MEMORY_DIRNAME,
    ensure_memory,
    list_files,
    memory_root,
    read_file,
)


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "demo"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)

    def tearDown(self):
        self._tmp.cleanup()


class TestEnsureMemory(MemoryTestCase):
    def test_creates_the_three_subdirs_and_status(self):
        root = ensure_memory(self.project)
        self.assertEqual(root, self.project / MEMORY_DIRNAME)
        for sub in ("knowledge", "decisions", "journal"):
            self.assertTrue((root / sub).is_dir(), sub)
        self.assertTrue((root / "STATUS.md").is_file())

    def test_adds_gitignore_entry(self):
        ensure_memory(self.project)
        lines = (self.project / ".gitignore").read_text().splitlines()
        self.assertIn(".memory/", [line.strip() for line in lines])

    def test_memory_is_its_own_git_repo(self):
        root = ensure_memory(self.project)
        self.assertTrue((root / ".git").exists())

    def test_is_idempotent_and_never_overwrites_status(self):
        root = ensure_memory(self.project)
        (root / "STATUS.md").write_text("# my own notes\n")
        ensure_memory(self.project)
        self.assertEqual((root / "STATUS.md").read_text(), "# my own notes\n")

    def test_preserves_existing_gitignore_content(self):
        (self.project / ".gitignore").write_text("node_modules/\n")
        ensure_memory(self.project)
        content = (self.project / ".gitignore").read_text()
        self.assertIn("node_modules/", content)
        self.assertIn(".memory/", content)

    def test_does_not_duplicate_gitignore_entry(self):
        ensure_memory(self.project)
        ensure_memory(self.project)
        lines = (self.project / ".gitignore").read_text().splitlines()
        self.assertEqual(len([l for l in lines if l.strip() == ".memory/"]), 1)

    def test_appends_newline_when_gitignore_lacks_trailing_one(self):
        (self.project / ".gitignore").write_text("*.log")
        ensure_memory(self.project)
        lines = (self.project / ".gitignore").read_text().splitlines()
        self.assertIn("*.log", [l.strip() for l in lines])
        self.assertIn(".memory/", [l.strip() for l in lines])


class TestListAndRead(MemoryTestCase):
    def test_list_is_empty_before_ensure(self):
        self.assertEqual(list_files(self.project), [])

    def test_lists_markdown_sorted_and_relative(self):
        root = ensure_memory(self.project)
        (root / "knowledge" / "tooling.md").write_text("pnpm\n")
        (root / "decisions" / "0001-db.md").write_text("postgres\n")
        self.assertEqual(
            list_files(self.project),
            ["STATUS.md", "decisions/0001-db.md", "knowledge/tooling.md"],
        )

    def test_ignores_non_markdown_and_git_internals(self):
        root = ensure_memory(self.project)
        (root / "knowledge" / "notes.txt").write_text("not markdown\n")
        self.assertEqual(list_files(self.project), ["STATUS.md"])

    def test_reads_a_file(self):
        root = ensure_memory(self.project)
        (root / "knowledge" / "tooling.md").write_text("pnpm\n")
        self.assertEqual(read_file(self.project, "knowledge/tooling.md"), "pnpm\n")

    def test_rejects_path_traversal(self):
        ensure_memory(self.project)
        (self.project / "secret.md").write_text("nope\n")
        with self.assertRaises(ValueError):
            read_file(self.project, "../secret.md")

    def test_rejects_absolute_path(self):
        ensure_memory(self.project)
        (self.project / "secret.md").write_text("nope\n")
        with self.assertRaises(ValueError):
            read_file(self.project, str(self.project / "secret.md"))

    def test_rejects_symlink_escape(self):
        root = ensure_memory(self.project)
        (self.project / "secret.md").write_text("nope\n")
        (root / "knowledge" / "escape.md").symlink_to(self.project / "secret.md")
        with self.assertRaises(ValueError):
            read_file(self.project, "knowledge/escape.md")

    def test_missing_file_raises_filenotfound(self):
        ensure_memory(self.project)
        with self.assertRaises(FileNotFoundError):
            read_file(self.project, "knowledge/absent.md")

    def test_rejects_git_internals(self):
        ensure_memory(self.project)
        with self.assertRaises(FileNotFoundError):
            read_file(self.project, ".git/config")


if __name__ == "__main__":
    unittest.main()
