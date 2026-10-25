"""Creating and deleting projects: what happens to their folders."""
import asyncio
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from dashboard.agents.manager import SessionManager
from dashboard.routers import projects


class TestDelete(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root / "projects"
        self.root.mkdir()
        self.chats = root / "chats"
        self._old_root = projects._projects_root
        projects.set_projects_root(self.root)
        self.patches = [
            unittest.mock.patch.object(projects.terminals, "is_running", return_value=False),
            unittest.mock.patch.object(projects, "manager", SessionManager(self.chats)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        projects.set_projects_root(self._old_root)
        self._tmp.cleanup()

    def create(self, name="p"):
        return projects.create_project(projects.CreateProjectRequest(name=name))

    def test_delete_keeps_the_folder_and_chats_by_default(self):
        self.create()
        (self.root / "p" / "work.txt").write_text("keep me")
        (self.chats / "p" / "codex").mkdir(parents=True)
        asyncio.run(projects.delete_project("p"))
        self.assertNotIn("p", projects._load_registry())
        self.assertEqual((self.root / "p" / "work.txt").read_text(), "keep me")
        self.assertTrue((self.chats / "p" / "codex").is_dir())

    def test_creating_it_again_takes_the_folder_up_as_it_is(self):
        self.create()
        (self.root / "p" / "CLAUDE.md").write_text("mine")
        asyncio.run(projects.delete_project("p"))
        meta = self.create()
        self.assertTrue(meta["adopted"])
        self.assertEqual((self.root / "p" / "CLAUDE.md").read_text(), "mine")
        self.assertIn("p", projects._load_registry())

    def test_delete_files_removes_folder_and_chats(self):
        self.create()
        (self.chats / "p" / "codex").mkdir(parents=True)
        asyncio.run(projects.delete_project("p", delete_files=True))
        self.assertFalse((self.root / "p").exists())
        self.assertFalse((self.chats / "p").exists())

    def test_classification_stops_agents_it_blocks(self):
        self.create()
        class Live:
            alive, stops = True, 0
            async def stop(self):
                self.stops += 1
        live = Live()
        with unittest.mock.patch.object(projects.manager, "peek",
                                        side_effect=lambda p, a: live if a == "claude" else None):
            r = asyncio.run(projects.set_classification(
                "p", projects.SetClassificationRequest(classification="work-restricted")))
        self.assertEqual(r["stopped"], ["claude"])
        self.assertEqual(live.stops, 1)
        self.assertEqual(projects._load_registry()["p"]["classification"], "work-restricted")

    def test_new_project_is_not_adopted(self):
        self.assertFalse(self.create("fresh")["adopted"])


if __name__ == "__main__":
    unittest.main()
