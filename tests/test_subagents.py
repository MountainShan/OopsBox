"""Subagents show as cards with their own steps, for Claude and Codex."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from dashboard.agents.claude import ClaudeSession
from dashboard.agents.codex import CodexSession
from dashboard.agents.session import ChatStore, ProjectInfo


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / "p").mkdir()
        self.store = root / "store"
        self.project = ProjectInfo(name="p", path=root / "p")

    def tearDown(self):
        self._tmp.cleanup()


class TestClaude(Base):
    async def test_a_subagent_and_its_steps(self):
        s = ClaudeSession(self.project, ChatStore(self.store))
        s.status = "working"
        await s._handle({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "A1", "name": "Agent",
             "input": {"subagent_type": "Explore", "description": "Find the login code", "prompt": "Look for login"}}]}})
        card = s.blocks["tu-A1"]
        self.assertEqual((card["tool"], card["status"], card["title"]), ("agent", "running", "Subagent: Find the login code"))
        self.assertEqual(card["subagent"]["type"], "Explore")
        self.assertEqual(s.subagents_running, 1)
        self.assertEqual(s.state()["subagents"], 1)

        # its own turns go into the card, not the conversation
        await s._handle({"type": "assistant", "parent_tool_use_id": "A1", "message": {"id": "m2", "content": [
            {"type": "text", "text": "Checking the auth module."},
            {"type": "tool_use", "id": "R1", "name": "Read", "input": {"file_path": str(self.project.path / "auth.py")}}]}})
        await s._handle({"type": "user", "parent_tool_use_id": "A1", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "R1", "content": "def login(): ..."}]}})
        await s._handle({"type": "stream_event", "parent_tool_use_id": "A1",
                         "event": {"type": "message_start", "message": {"id": "inner"}}})
        sa = s.blocks["tu-A1"]["subagent"]
        self.assertEqual(sa["steps"], [{"id": "R1", "title": "Read auth.py", "status": "completed"}])
        self.assertEqual((sa["count"], sa["last"]), (1, "Checking the auth module."))
        self.assertFalse(any(b["kind"] == "assistant" for b in s.blocks.values()))

        await s._handle({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "A1", "content": [{"type": "text", "text": "login is in auth.py"}]}]}})
        card = s.blocks["tu-A1"]
        self.assertEqual((card["status"], card["output"]), ("completed", "login is in auth.py"))
        self.assertIn("ended", card["subagent"])
        self.assertEqual(s.subagents_running, 0)

    async def test_the_older_task_tool_too(self):
        s = ClaudeSession(self.project, ChatStore(self.store))
        await s._handle({"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "T1", "name": "Task", "input": {"description": "Review"}}]}})
        self.assertEqual(s.blocks["tu-T1"]["tool"], "agent")

    async def test_cards_left_running_are_closed(self):
        s = ClaudeSession(self.project, ChatStore(self.store))
        s.status = "working"
        s.subagent_start("tu-X", "Explore", "x")
        s.set_status("stopped")
        self.assertEqual(s.blocks["tu-X"]["status"], "failed")
        # and from disk, after OopsBox restarted mid-run
        s2 = ClaudeSession(self.project, ChatStore(self.store))
        s2.subagent_start("tu-Y", "Explore", "y")
        s3 = ClaudeSession(self.project, ChatStore(self.store))
        self.assertEqual(s3.blocks["tu-Y"]["status"], "interrupted")  # as any tool left running
        self.assertEqual(s3.subagents_running, 0)

    async def test_steps_are_capped(self):
        s = ClaudeSession(self.project, ChatStore(self.store))
        s.subagent_start("tu-Z", "", "many")
        for i in range(s.SUBAGENT_STEPS + 5):
            s.subagent_step("tu-Z", f"s{i}", f"step {i}")
        sa = s.blocks["tu-Z"]["subagent"]
        self.assertEqual((len(sa["steps"]), sa["count"]), (s.SUBAGENT_STEPS, s.SUBAGENT_STEPS + 5))
        self.assertEqual(sa["steps"][-1]["title"], f"step {s.SUBAGENT_STEPS + 4}")


class TestCodex(Base):
    async def test_spawned_agents_and_their_threads(self):
        s = CodexSession(self.project, ChatStore(self.store))
        s.status = "working"
        s._thread_id = "main"
        spawn = {"type": "collabAgentToolCall", "id": "c1", "tool": "spawnAgent", "status": "completed",
                 "senderThreadId": "main", "receiverThreadIds": ["sub1"], "prompt": "Audit the tests\nin detail",
                 "model": "gpt-5", "agentsStates": {"sub1": {"status": "running", "message": None}}}
        await s._on_notification("item/completed", {"threadId": "main", "item": spawn})
        card = s.blocks["sa-sub1"]
        self.assertEqual((card["title"], card["subagent"]["type"], card["status"]),
                         ("Subagent: Audit the tests", "gpt-5", "running"))
        # the subagent's own thread
        await s._on_notification("item/started", {"threadId": "sub1", "item": {
            "type": "commandExecution", "id": "x1", "command": "pytest -q", "status": "inProgress"}})
        await s._on_notification("item/completed", {"threadId": "sub1", "item": {
            "type": "commandExecution", "id": "x1", "command": "pytest -q", "status": "completed", "exitCode": 1}})
        await s._on_notification("item/completed", {"threadId": "sub1", "item": {
            "type": "agentMessage", "id": "m1", "text": "Two tests fail."}})
        sa = s.blocks["sa-sub1"]["subagent"]
        self.assertEqual(sa["steps"], [{"id": "x1", "title": "pytest -q", "status": "failed"}])
        self.assertEqual(sa["last"], "Two tests fail.")
        self.assertNotIn("m1", s.blocks)  # not in the main conversation
        # wait, then the subagent is done
        wait = {"type": "collabAgentToolCall", "id": "c2", "tool": "wait", "status": "completed",
                "senderThreadId": "main", "receiverThreadIds": ["sub1"], "prompt": None,
                "agentsStates": {"sub1": {"status": "completed", "message": "Two tests fail."}}}
        await s._on_notification("item/completed", {"threadId": "main", "item": wait})
        self.assertEqual(s.blocks["c2"]["title"], "Wait for subagents")
        self.assertEqual((s.blocks["sa-sub1"]["status"], s.blocks["sa-sub1"]["output"]), ("completed", "Two tests fail."))
        self.assertEqual(s.subagents_running, 0)

    async def test_other_threads_are_still_ignored(self):
        s = CodexSession(self.project, ChatStore(self.store))
        s._thread_id = "main"
        await s._on_notification("item/completed", {"threadId": "stranger", "item": {
            "type": "agentMessage", "id": "m9", "text": "hi"}})
        self.assertNotIn("m9", s.blocks)


if __name__ == "__main__":
    unittest.main()
