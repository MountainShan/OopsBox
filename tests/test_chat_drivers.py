"""Chat sessions end to end, against scripted fake agents (tests/fake_agents.py)."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from dashboard.agents.acp import KiroSession
from dashboard.agents.codex import CodexSession
from dashboard.agents.session import AgentSession, ChatStore, PermissionOption, ProjectInfo

FAKE = str(Path(__file__).with_name("fake_agents.py"))


class FakeAcp(KiroSession):
    command = [sys.executable, FAKE, "acp"]


class FakeCodex(CodexSession):
    command = [sys.executable, FAKE, "codex"]


async def wait_for(pred, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not pred():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def pending_permission(session: AgentSession):
    return next((b for b in session.blocks.values()
                 if b["kind"] == "permission" and b.get("status") == "pending"), None)


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.project_dir = root / "proj"
        self.project_dir.mkdir()
        self.store_dir = root / "chat"
        self.project = ProjectInfo(name="proj", path=self.project_dir)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, cls, instructions="MEMORY-NOTE"):
        return cls(self.project, ChatStore(self.store_dir), instructions)


class TestAcpDriver(Base):
    async def test_turn_with_permission(self):
        s = self.make(FakeAcp)
        q = s.subscribe()
        await s.prompt("hi")
        await wait_for(lambda: pending_permission(s) is not None)
        perm = pending_permission(s)
        self.assertEqual([o["id"] for o in perm["options"]], ["yes", "no"])
        self.assertTrue(s.waiting)
        self.assertTrue(s.resolve_permission(perm["id"], "yes"))
        await wait_for(lambda: s.status == "idle")
        self.assertFalse(s.waiting)

        kinds = [b["kind"] for b in s.blocks.values()]
        self.assertEqual(kinds[0], "user")
        assistant = next(b for b in s.blocks.values() if b["kind"] == "assistant")
        # the memory note rode along as an extra prompt part on the first turn
        self.assertEqual(assistant["text"], "got 2 parts. working")
        tool = s.blocks["tc-t1"]
        self.assertEqual(tool["status"], "completed")
        self.assertIn("chose yes", tool["output"])

        events = []
        while not q.empty():
            events.append(q.get_nowait())
        self.assertEqual(events[0]["type"], "reset")
        self.assertTrue(any(e["type"] == "delta" for e in events))
        await s.stop()

    async def test_second_turn_has_no_instructions(self):
        s = self.make(FakeAcp)
        s.autopilot = True
        await s.prompt("one")
        await wait_for(lambda: s.status == "idle")
        await s.prompt("two")
        await wait_for(lambda: s.status == "idle")
        texts = [b["text"] for b in s.blocks.values() if b["kind"] == "assistant"]
        self.assertEqual(texts, ["got 2 parts. working", "got 1 parts. working"])
        await s.stop()

    async def test_history_survives_restart(self):
        s = self.make(FakeAcp)
        s.autopilot = True
        await s.prompt("hi")
        await wait_for(lambda: s.status == "idle")
        before = [(b["id"], b["kind"]) for b in s.blocks.values()]
        await s.stop()

        again = self.make(FakeAcp)
        self.assertEqual([(b["id"], b["kind"]) for b in again.blocks.values()], before)
        self.assertEqual(again.blocks["tc-t1"]["status"], "completed")
        self.assertEqual(again.saved.get("session_id"), "s1")


class TestFeatures(Base):
    async def test_codex_lists_visible_models_and_modes(self):
        s = self.make(FakeCodex)
        await s.start()
        self.assertEqual([m["id"] for m in s.models], ["m1"])
        self.assertEqual(s.models[0]["efforts"], ["low", "high"])
        self.assertEqual(s.mode, "auto")
        self.assertIn("/compact", [c["name"] for c in s.commands])
        await s.set_model("m1", "high")
        self.assertEqual(s.saved["model"], "m1")
        self.assertEqual(s.saved["effort"], "high")
        with self.assertRaises(ValueError):
            await s.set_model("nope", None)
        await s.stop()

    async def test_acp_mode_switch(self):
        s = self.make(FakeAcp)
        await s.start()
        self.assertEqual([m["id"] for m in s.modes], ["a", "b"])
        await s.set_mode("b")
        self.assertEqual(s.mode, "b")
        await s.stop()

    async def test_message_while_working_is_queued_then_sent(self):
        s = self.make(FakeAcp)
        s.can_steer = False  # the queue is for agents that cannot take a message mid-turn
        await s.prompt("one")
        await wait_for(lambda: pending_permission(s) is not None)  # turn is held open
        await s.prompt("two")
        queued = [b for b in s.blocks.values() if b["kind"] == "user" and b.get("queued")]
        self.assertEqual([b["text"] for b in queued], ["two"])
        self.assertEqual(s.state()["queued"], 1)
        s.set_autopilot(True)
        await wait_for(lambda: len([b for b in s.blocks.values() if b["kind"] == "assistant"]) == 2
                       and s.status == "idle")
        self.assertFalse(any(b.get("queued") for b in s.blocks.values()))
        await s.stop()

    async def test_question_round_trip(self):
        s = self.make(FakeAcp)
        task = asyncio.create_task(s.ask_question("q1", [{"id": "0", "question": "pick", "options": []}]))
        await wait_for(lambda: "q1" in s.blocks)
        self.assertTrue(s.answer_question("q1", {"0": "red"}))
        self.assertEqual(await task, {"0": ["red"]})
        self.assertEqual(s.blocks["q1"]["status"], "answered")

    async def test_new_conversation_archives_history(self):
        s = self.make(FakeAcp)
        s.autopilot = True
        await s.prompt("hi")
        await wait_for(lambda: s.status == "idle")
        await s.new_conversation()
        self.assertEqual(s.blocks, {})
        self.assertTrue(any(p.name.startswith("blocks-") for p in self.store_dir.iterdir()))
        self.assertEqual(s.status, "idle")
        await s.stop()

    async def test_killed_agent_reads_as_stopped(self):
        s = self.make(FakeAcp)
        s.agent_exited(143, "")
        self.assertEqual(s.status, "stopped")
        s.agent_exited(1, "boom")
        self.assertEqual(s.status, "error")


class TestCodexDriver(Base):
    async def test_turn_with_declined_command(self):
        s = self.make(FakeCodex)
        await s.prompt("go")
        await wait_for(lambda: pending_permission(s) is not None)
        perm = pending_permission(s)
        self.assertIn("touch x", perm["detail"])  # bash wrapper stripped
        s.resolve_permission(perm["id"], "decline")
        await wait_for(lambda: s.status == "idle")
        self.assertEqual(s.blocks["a1"]["text"], "hello")
        self.assertEqual(s.blocks["c1"]["status"], "declined")
        self.assertEqual(s.blocks["c1"]["title"], "touch x")
        self.assertEqual(s.saved.get("thread_id"), "th1")
        self.assertEqual(s.terminal_command(), ["codex", "resume", "th1"])  # it had a turn
        await s.stop()

    async def test_empty_thread_starts_over_quietly(self):
        notices = lambda s: [b["text"] for b in s.blocks.values() if b["kind"] == "notice"]
        s = self.make(FakeCodex)
        s.save_state(thread_id="empty")
        await s.start()
        self.assertEqual(s.saved["thread_id"], "th1")
        self.assertFalse(any("Could not resume" in t for t in notices(s)))
        await s.stop()
        s.save_state(thread_id="broken")
        await s.start()
        self.assertTrue(any("Could not resume" in t for t in notices(s)))
        await s.stop()

    async def test_autopilot_accepts(self):
        s = self.make(FakeCodex)
        s.set_autopilot(True)
        await s.prompt("go")
        await wait_for(lambda: s.status == "idle")
        self.assertEqual(s.blocks["c1"]["status"], "completed")
        self.assertEqual(s.blocks["c1"]["output"], "done")
        perm = next(b for b in s.blocks.values() if b["kind"] == "permission")
        self.assertTrue(perm["auto"])
        await s.stop()

    async def test_turning_autopilot_on_answers_waiting_request(self):
        s = self.make(FakeCodex)
        await s.prompt("go")
        await wait_for(lambda: pending_permission(s) is not None)
        s.set_autopilot(True)
        await wait_for(lambda: s.status == "idle")
        self.assertEqual(s.blocks["c1"]["status"], "completed")
        await s.stop()


class TestSessionBasics(Base):
    def test_store_marks_cut_off_blocks_interrupted(self):
        store = ChatStore(self.store_dir)
        store.append_block({"id": "x", "kind": "tool", "status": "running"})
        store.append_block({"id": "y", "kind": "assistant", "text": "a"})
        store.append_block({"id": "y", "kind": "assistant", "text": "ab"})
        blocks = store.load_blocks()
        self.assertEqual([b["id"] for b in blocks], ["x", "y"])
        self.assertEqual(blocks[0]["status"], "interrupted")
        self.assertEqual(blocks[1]["text"], "ab")

    async def test_unknown_option_is_rejected(self):
        s = self.make(FakeAcp)
        task = asyncio.create_task(s.ask_permission(
            "p1", "t", "d", [PermissionOption("ok", "OK", "allow_once")]))
        await wait_for(lambda: "p1" in s.blocks)
        self.assertFalse(s.resolve_permission("p1", "nope"))
        self.assertTrue(s.resolve_permission("p1", "ok"))
        self.assertEqual(await task, "ok")
        self.assertFalse(s.resolve_permission("p1", "ok"))  # already answered

    async def test_stop_cancels_waiting_permission(self):
        s = self.make(FakeAcp)
        task = asyncio.create_task(s.ask_permission(
            "p1", "t", "d", [PermissionOption("ok", "OK", "allow_once")]))
        await wait_for(lambda: "p1" in s.blocks)
        await s.stop()
        self.assertEqual(await task, "")
        self.assertEqual(s.blocks["p1"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
