"""Terminal daemons: what gets started, and how."""
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from dashboard import terminals


class Done:
    def __init__(self, rc=0, err=""):
        self.returncode, self.stderr, self.stdout = rc, err, ""


class TestStart(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_start(self, active=False, **kw):
        def fake_run(argv, **_):
            self.calls.append(argv)
            if argv[:3] == ["systemctl", "--user", "is-active"]:
                return Done(0 if active else 3)
            return Done()
        with unittest.mock.patch.object(terminals.subprocess, "run", side_effect=fake_run), \
             unittest.mock.patch.object(terminals.shutil, "which", return_value="/usr/bin/dtach"), \
             unittest.mock.patch.object(terminals, "_state_dir", return_value=self.state), \
             unittest.mock.patch.object(terminals, "_update_nginx"):
            return terminals.start("proj", Path("/work"), **kw)

    def test_shell_held_by_dtach_in_a_user_unit(self):
        port = self.run_start()
        argv = next(a for a in self.calls if a[0] == "systemd-run")
        self.assertEqual(argv[1:5], ["--user", "--unit", "oopsbox-term-proj", "--collect"])
        self.assertIn("WorkingDirectory=/work", argv)
        ttyd = argv[argv.index("ttyd"):]
        self.assertEqual(ttyd[ttyd.index("--interface") + 1], "lo")
        self.assertEqual(ttyd[ttyd.index("--base-path") + 1], "/terminal/proj")
        self.assertEqual(ttyd[ttyd.index("--") + 1:ttyd.index("--") + 4], ["dtach", "-A", str(self.state / "shell.dtach")])
        self.assertNotIn("tmux", " ".join(argv))
        self.assertEqual((self.state / "ttyd.port").read_text(), str(port))

    def test_running_daemon_is_reused(self):
        (self.state / "ttyd.port").write_text("4242")
        self.assertEqual(self.run_start(active=True), 4242)
        self.assertFalse(any(a[0] == "systemd-run" for a in self.calls))

    def test_secrets_go_in_the_environment(self):
        self.run_start(first_command="sshpass -e ssh host", env={"SSHPASS": "pw"})
        argv = next(a for a in self.calls if a[0] == "systemd-run")
        self.assertIn("SSHPASS=pw", argv)
        self.assertNotIn("pw", argv[argv.index("ttyd"):])

    def test_needs_dtach(self):
        with unittest.mock.patch.object(terminals.shutil, "which", return_value=None):
            with self.assertRaises(terminals.TerminalError):
                terminals.start("proj", Path("/work"))



class TestSshFields(unittest.TestCase):
    def test_option_smuggling_rejected(self):
        from dashboard.agents.launch import ssh_target
        self.assertEqual(ssh_target("me", "host.example", "2222", None), ("me@host.example", 2222, None))
        for user, host, port, key in (("-oProxyCommand=sh", "h", 22, None), ("me", "-oProxyCommand=x", 22, None),
                                      ("me", "h", "22; rm", None), ("me", "h", 22, "-oFoo"), ("", "h", 22, None)):
            with self.assertRaises(ValueError):
                ssh_target(user, host, port, key)

    def test_remote_terminal_ends_options_before_the_host(self):
        from dashboard.routers.projects import _remote_shell
        cmd, env = _remote_shell({"type": "ssh", "ssh_user": "me", "ssh_host": "h", "ssh_password": "pw",
                                  "remote_path": "~/work"})
        self.assertIn("-- me@h", cmd)
        self.assertEqual(env, {"SSHPASS": "pw"})
        self.assertNotIn("pw", cmd)


if __name__ == "__main__":
    unittest.main()


class TestShells(unittest.TestCase):
    def test_running_reads_active_units(self):
        out = ("oopsbox-term-_t1.service loaded active running /usr/local/bin/ttyd …\n"
               "oopsbox-term-acme-api.service loaded active running /usr/local/bin/ttyd …\n")
        done = Done(); done.stdout = out
        with unittest.mock.patch.object(terminals.subprocess, "run", return_value=done):
            self.assertEqual(terminals.running(), ["_t1", "acme-api"])

    def test_shell_names(self):
        from dashboard.routers.projects import is_shell, next_shell
        self.assertTrue(is_shell("_t1") and is_shell("_t12") and is_shell("_global"))
        self.assertFalse(is_shell("_t0") or is_shell("_t") or is_shell("_tx") or is_shell("t1") or is_shell("_t1/../x"))
        self.assertEqual(next_shell([]), "_t1")
        self.assertEqual(next_shell(["_t1", "_t3", "proj"]), "_t2")
