"""Agents running when OopsBox stops come back when it starts again."""
import sys
import tempfile
import unittest
from pathlib import Path

from dashboard.agents import manager as manager_mod
from dashboard.agents.manager import SessionManager
from tests.test_chat_drivers import FakeAcp, FakeCodex


class TestStandby(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "proj").mkdir()
        self.meta = {"name": "proj", "path": str(root / "proj"), "type": "local"}
        self.root = root / "chats"
        self._drivers = dict(manager_mod.DRIVERS)
        manager_mod.DRIVERS.update(kiro=FakeAcp, codex=FakeCodex)

    async def asyncTearDown(self):
        manager_mod.DRIVERS.clear()
        manager_mod.DRIVERS.update(self._drivers)
        self._tmp.cleanup()

    async def test_shutdown_keeps_standby_and_restore_starts_it(self):
        m = SessionManager(self.root)
        await m.get(self.meta, "kiro").start()
        await m.shutdown()
        again = SessionManager(self.root)
        self.assertEqual(await again.restore(self.meta, ["codex", "claude", "kiro"]), ["kiro"])
        self.assertTrue(again.peek("proj", "kiro").alive)
        await again.shutdown()

    async def test_stopped_by_someone_stays_stopped(self):
        m = SessionManager(self.root)
        s = m.get(self.meta, "kiro")
        await s.start()
        await m.stop_project("proj")
        self.assertEqual(await SessionManager(self.root).restore(self.meta, ["kiro"]), [])

    async def test_handed_off_or_blocked_is_not_restored(self):
        m = SessionManager(self.root)
        k, c = m.get(self.meta, "kiro"), m.get(self.meta, "codex")
        await k.start(); await c.start()
        await m.shutdown()
        k.save_state(handed_off=True)
        # codex is no longer allowed on the project
        self.assertEqual(await SessionManager(self.root).restore(self.meta, ["kiro", "claude"]), [])


if __name__ == "__main__":
    unittest.main()
