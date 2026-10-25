"""Slash commands, launch arguments, other conversations, against the fake kiro."""
import asyncio
import json
import unittest
from pathlib import Path

from tests.test_chat_drivers import Base, FakeAcp, pending_permission, wait_for


def command_blocks(s):
    return [b for b in s.blocks.values() if b["kind"] == "command"]


class TestCommands(Base):
    async def asyncSetUp(self):
        self.s = self.make(FakeAcp)
        await self.s.start()
        await wait_for(lambda: self.s.command_spec("/mcp") is not None)

    async def asyncTearDown(self):
        await self.s.stop()

    async def test_kinds_come_from_kiro_meta(self):
        kinds = {c["name"]: c["kind"] for c in self.s.commands}
        self.assertEqual(kinds["/model"], "selection")
        self.assertEqual(kinds["/mcp"], "panel")
        self.assertEqual(kinds["/clear"], "action")
        self.assertEqual(kinds["/chat"], "local")
        self.assertEqual(kinds["/voice"], "terminal")
        self.assertEqual(self.s.command_spec("/mcp")["subcommands"][0]["name"], "list")

    async def test_panel_command_creates_block(self):
        block = await self.s.run_command("/mcp", "")
        self.assertEqual(block["status"], "ok")
        self.assertEqual(block["message"], "1 MCP server configured")
        self.assertEqual(block["data"]["servers"][0]["name"], "ssh")

    async def test_value_is_passed_as_args(self):
        block = await self.s.run_command("/clear", "all")
        self.assertEqual(block["message"], "ran clear all")

    async def test_selection_without_value_lists_options(self):
        block = await self.s.run_command("/model", "")
        self.assertEqual(block["status"], "choose")
        self.assertEqual([o["value"] for o in block["options"]], ["m1", "m2"])
        self.assertTrue(block["options"][0]["active"])
        self.assertEqual(block["options"][0]["description"], "first")
        picked = await self.s.run_command("/model", "m2", block_id=block["id"])
        self.assertEqual(picked["id"], block["id"])
        self.assertEqual(picked["status"], "ok")
        self.assertIsNone(picked["options"])

    async def test_missing_method_falls_back_to_terminal(self):
        block = await self.s.run_command("/gone", "")
        self.assertEqual(block["status"], "error")
        self.assertTrue(block["handoff"])
        self.assertEqual(self.s.command_spec("/gone")["kind"], "terminal")

    async def test_prompt_routes_commands_and_keeps_text(self):
        await self.s.prompt("/mcp")
        await wait_for(lambda: any(b["status"] == "ok" for b in command_blocks(self.s)))
        self.assertFalse(any(b["kind"] == "user" for b in self.s.blocks.values()))
        await self.s.prompt("/unknown thing")  # not a command: goes to the model as text
        await wait_for(lambda: pending_permission(self.s) is not None)
        user = [b for b in self.s.blocks.values() if b["kind"] == "user"]
        self.assertEqual(user[0]["text"], "/unknown thing")

    async def test_command_while_working_runs(self):
        await self.s.prompt("hi")
        await wait_for(lambda: pending_permission(self.s) is not None)
        block = await self.s.run_command("/mcp", "")
        self.assertEqual(block["status"], "ok")

    async def test_steer_while_working(self):
        await self.s.prompt("hi")
        await wait_for(lambda: pending_permission(self.s) is not None)
        await self.s.prompt("also this")
        self.assertEqual(self.s.state()["queued"], 0)
        self.assertTrue(self.s.can_steer)

    async def test_chat_without_value_opens_conversations(self):
        block = await self.s.run_command("/chat", "")
        self.assertEqual(block["data"], {"open": "conversations"})


class TestLaunchArgs(Base):
    def test_reserved_flag_rejected(self):
        s = self.make(FakeAcp)
        s.reserved_flags = frozenset({"--help"})
        with self.assertRaises(ValueError):
            s.parse_extra_args("--model x --help")

    def test_bad_quoting_rejected(self):
        with self.assertRaises(ValueError):
            self.make(FakeAcp).parse_extra_args('--agent "unclosed')

    async def test_extra_args_saved_and_shown(self):
        s = self.make(FakeAcp)
        await s.set_extra_args('--agent "my agent" --trust-tools=fs_read')
        self.assertEqual(s.saved["extra_args"], ["--agent", "my agent", "--trust-tools=fs_read"])
        self.assertEqual(s.state()["extra_args"], "--agent 'my agent' --trust-tools=fs_read")

    async def test_extra_args_reach_the_command_line(self):
        s = self.make(FakeAcp)
        s.command = s.command + ["acp-args"]  # fake_agents ignores unknown argv
        await s.set_extra_args("--verbose")
        await s.start()
        self.assertEqual(s._rpc.argv[-1], "--verbose")
        await s.stop()


class TestConversations(Base):
    # kiro marks sessions made over ACP as "subagent", so the reason is no filter
    def write_session(self, home, sid, cwd, updated, reason="subagent"):
        d = home / ".kiro" / "sessions" / "cli"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.json").write_text(json.dumps({
            "session_id": sid, "cwd": str(cwd), "title": f"t-{sid}",
            "updated_at": updated, "session_created_reason": reason}))

    async def test_lists_only_this_projects_sessions(self):
        home = Path(self._tmp.name) / "home"
        self.write_session(home, "a", self.project_dir, "2026-10-24T23:17:46.716701753Z")
        self.write_session(home, "b", self.project_dir, "2026-10-25T01:00:00Z")
        self.write_session(home, "c", "/elsewhere", "2026-10-25T02:00:00Z")
        s = self.make(FakeAcp)
        s.saved["session_id"] = "a"
        with unittest.mock.patch("pathlib.Path.home", return_value=home):
            convs = await s.list_conversations()
        self.assertEqual([c["id"] for c in convs], ["b", "a"])
        self.assertTrue(convs[1]["current"])
        self.assertGreater(convs[1]["updated"], 1.7e9)

    def test_terminal_command_resumes(self):
        s = self.make(FakeAcp)
        s.saved["session_id"] = "abc"
        self.assertEqual(s.terminal_command(), ["kiro-cli", "chat", "--resume-id", "abc"])


class TestCodexCommands(Base):
    async def asyncSetUp(self):
        from tests.test_chat_drivers import FakeCodex
        self.s = self.make(FakeCodex)
        await self.s.start()

    async def asyncTearDown(self):
        await self.s.stop()

    async def test_permissions_list_marks_current(self):
        block = await self.s.run_command("/permissions", "")
        self.assertEqual(block["status"], "choose")
        self.assertEqual([o["value"] for o in block["options"] if o["active"]], ["auto"])
        await self.s.run_command("/permissions", "read-only", block["id"])
        self.assertEqual(self.s.mode, "read-only")

    async def test_plan_toggles_and_reaches_the_thread(self):
        block = await self.s.run_command("/plan", "")
        self.assertEqual(block["status"], "ok")
        self.assertTrue(self.s.saved["plan"])
        self.assertEqual(self.s._turn_settings()["collaborationMode"]["mode"], "plan")
        seen = await self.s._rpc.request("test/names", {})
        self.assertEqual(seen["settings"][-1]["collaborationMode"]["mode"], "plan")
        await self.s.run_command("/plan", "")
        self.assertNotIn("collaborationMode", self.s._turn_settings())

    async def test_rename_calls_codex(self):
        await self.s.run_command("/rename", "my thread")
        seen = await self.s._rpc.request("test/names", {})
        self.assertEqual(seen["names"], ["my thread"])

    async def test_unsupported_method_becomes_terminal(self):
        block = await self.s.run_command("/hooks", "")  # the fake has no hooks/list
        self.assertTrue(block["handoff"])
        self.assertEqual(self.s.command_spec("/hooks")["kind"], "terminal")

    async def test_terminal_only_commands_hand_off(self):
        block = await self.s.run_command("/keymap", "")
        self.assertTrue(block["handoff"])

    async def test_conversations_come_from_thread_list(self):
        convs = await self.s.list_conversations()
        self.assertEqual([c["id"] for c in convs], ["th1", "th0"])
        self.assertTrue(convs[0]["current"])
        self.assertEqual(convs[1]["title"], "older")
        self.assertGreater(convs[1]["updated"], 1.7e9)
        # th1 has not had a turn, so codex has nothing on disk to resume
        self.assertEqual(self.s.terminal_command(), ["codex"])
        self.s.save_state(on_disk="th1")
        self.assertEqual(self.s.terminal_command(), ["codex", "resume", "th1"])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
