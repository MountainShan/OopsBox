"""The shared activity log: every agent's turns, written by OopsBox."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dashboard import memory
from dashboard.agents import launch
from dashboard.agents.session import AgentSession, ProjectInfo


class TestActivity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        memory.ensure_memory(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def read(self):
        return (self.dir / ".memory" / "activity.md").read_text()

    def test_entry(self):
        memory.append_activity(self.dir, "claude", "Sync the\nskills", "Done:\n4 linked.", ["Edit a.py", "Edit a.py", "Write b.md"])
        text = self.read()
        self.assertTrue(text.startswith("# Activity"))
        self.assertIn("· claude\n- Asked: Sync the skills\n- Changed: Edit a.py, Write b.md\n- Answered: Done: 4 linked.", text)

    def test_keeps_the_last_entries(self):
        for i in range(memory.ACTIVITY_KEEP + 5):
            memory.append_activity(self.dir, "codex", f"task {i}", "ok")
        text = self.read()
        self.assertEqual(text.count("\n### "), memory.ACTIVITY_KEEP)
        self.assertNotIn("task 4\n", text)
        self.assertIn(f"task {memory.ACTIVITY_KEEP + 4}", text)

    def test_long_text_is_cut(self):
        memory.append_activity(self.dir, "kiro", "x" * 1000, "y" * 5000)
        line = [l for l in self.read().splitlines() if l.startswith("- Answered")][0]
        self.assertLessEqual(len(line), 620)

    def test_agents_see_the_recent_entries(self):
        memory.append_activity(self.dir, "claude", "Link the Notion skills for codex and kiro", "Linked 4 skills.")
        text = launch.memory_instructions(ProjectInfo(name="p", path=self.dir))
        self.assertIn("Recent activity on this project", text)
        self.assertIn("Link the Notion skills", text)
        self.assertIn("activity.md", text)

    def test_a_finished_turn_is_recorded(self):
        blocks = [
            {"id": "u0", "kind": "user", "text": "older request"},
            {"id": "u1", "kind": "user", "text": "Fix the login"},
            {"id": "t1", "kind": "tool", "tool": "edit", "title": "Edit auth.py"},
            {"id": "t2", "kind": "tool", "tool": "execute", "title": "pytest"},
            {"id": "a1", "kind": "assistant", "text": "Fixed it."},
        ]
        fake = SimpleNamespace(blocks={b["id"]: b for b in blocks}, agent="codex", project=ProjectInfo(name="p", path=self.dir))
        AgentSession._record_turn(fake)
        text = self.read()
        self.assertIn("- Asked: Fix the login\n- Changed: Edit auth.py\n- Answered: Fixed it.", text)
        self.assertNotIn("older request", text)


if __name__ == "__main__":
    unittest.main()
