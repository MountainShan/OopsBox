"""Claude Code transcripts: listing past conversations and drawing one."""
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from dashboard.agents.claude import ClaudeSession, _user_text
from dashboard.agents.session import ChatStore, ProjectInfo


class TestUserText(unittest.TestCase):
    def test_plain_and_parts(self):
        self.assertEqual(_user_text({"content": "hi"}), "hi")
        self.assertEqual(_user_text({"content": [{"type": "text", "text": "a"}, {"type": "image"}]}), "a")

    def test_tool_results_are_not_typed_text(self):
        self.assertEqual(_user_text({"content": [{"type": "tool_result", "content": "x"}]}), "")

    def test_slash_command_shown_as_typed(self):
        text = "<command-message>init</command-message>\n<command-name>/init</command-name>\n<command-args>now</command-args>"
        self.assertEqual(_user_text({"content": text}), "/init now")
        self.assertEqual(_user_text({"content": "<local-command-stdout>x</local-command-stdout>"}), "")


class TestTranscripts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"
        self.proj = root / "my_proj"
        self.proj.mkdir()
        enc = "".join(c if c.isalnum() else "-" for c in str(self.proj.resolve()))
        self.dir = self.home / ".claude" / "projects" / enc
        self.dir.mkdir(parents=True)
        lines = [
            {"type": "queue-operation"},
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "first question"}},
            {"type": "assistant", "message": {"id": "m1", "content": [
                {"type": "text", "text": "answer"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a.txt"}]}},
            {"type": "user", "isMeta": True, "message": {"content": "Continue"}},
            {"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued"}},
        ]
        (self.dir / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in lines))
        (self.dir / "empty.jsonl").write_text(json.dumps({"type": "queue-operation"}))
        self.s = ClaudeSession(ProjectInfo(name="p", path=self.proj), ChatStore(root / "store"))

    def tearDown(self):
        self._tmp.cleanup()

    def test_lists_titled_conversations(self):
        self.s.saved["session_id"] = "s1"
        with unittest.mock.patch("pathlib.Path.home", return_value=self.home):
            convs = self.s._scan_conversations()
        self.assertEqual([(c["id"], c["title"], c["current"]) for c in convs], [("s1", "first question", True)])

    def test_history_drawn_with_tools_completed(self):
        self.s.saved["session_id"] = "s1"
        with unittest.mock.patch("pathlib.Path.home", return_value=self.home):
            self.s._render_history()
        kinds = [(b["kind"], b.get("text") or b.get("title")) for b in self.s.blocks.values()]
        self.assertEqual(kinds, [("user", "first question"), ("assistant", "answer"), ("tool", "ls")])
        self.assertEqual(self.s.blocks["tu-t1"]["status"], "completed")
        self.assertEqual(self.s.blocks["h-u1"]["message_uuid"], "u1")

    def test_terminal_command_resumes(self):
        self.s.saved["session_id"] = "s1"
        self.s.saved["extra_args"] = ["--chrome"]
        self.assertEqual(self.s.terminal_command(), ["claude", "--resume", "s1", "--chrome"])



class TestModelNames(unittest.TestCase):
    def test_versions_shown(self):
        from dashboard.agents.claude import _model_entry
        e = _model_entry({"value": "sonnet", "resolvedModel": "claude-sonnet-5", "displayName": "Sonnet",
                          "description": "Sonnet 5 · Efficient for routine tasks"})
        self.assertEqual((e["name"], e["resolved"], e["description"]),
                         ("Sonnet 5", "claude-sonnet-5", "Efficient for routine tasks"))
        e = _model_entry({"value": "default", "displayName": "Default (recommended)",
                          "description": "Opus 5 with 1M context · Best for everyday, complex tasks"})
        self.assertEqual(e["name"], "Default (recommended) · Opus 5 (1M)")



class TestOtherModels(unittest.IsolatedAsyncioTestCase):
    async def test_listed_models_are_not_repeated(self):
        from dashboard.agents.claude import ClaudeSession
        s = ClaudeSession(ProjectInfo(name="p", path=Path("/tmp")), ChatStore(Path(tempfile.mkdtemp())))
        init = {"models": [
            {"value": "default", "resolvedModel": "claude-opus-5[1m]", "displayName": "Default", "description": "Opus 5 · x"},
            {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku", "description": "Haiku 4.5 · x"}]}

        async def fake_control(req, timeout):
            return init
        s._control_request = fake_control
        s._spawn = lambda coro: coro.close()

        async def no_proc(*a, **k):
            class P:
                stdout = stderr = None
            return P()
        import asyncio as aio
        orig = aio.create_subprocess_exec
        aio.create_subprocess_exec = no_proc
        try:
            await s._agent_start()
        finally:
            aio.create_subprocess_exec = orig
        ids = [m["id"] for m in s.models]
        self.assertIn("claude-sonnet-5-5", ids)
        self.assertNotIn("claude-opus-5", ids)      # the default already is Opus 5
        self.assertNotIn("claude-haiku-4-5", ids)   # listed as claude-haiku-4-5-20251001
        self.assertEqual(next(m for m in s.models if m["id"] == "claude-opus-4-8")["group"], "Other versions")


if __name__ == "__main__":
    unittest.main()
