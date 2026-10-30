"""SSH projects: agents run here, in the remote folder mounted over sshfs."""
import asyncio
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from dashboard.agents import launch, remote
from dashboard.agents.session import ProjectInfo


def ssh_project(**kw):
    return ProjectInfo(name="box", path=Path("/tmp/projects/box"), type="ssh", ssh_host="10.0.0.5",
                       ssh_user="ops", ssh_password="pw", remote_path="/srv/my app", **kw)


class TestLaunch(unittest.TestCase):
    def test_ssh_agents_run_here_in_the_mount(self):
        p = ssh_project()
        argv, cwd, env = launch.build_command(p, ["claude", "-p"], {"X": "1"})
        self.assertEqual(argv, ["claude", "-p"])  # not wrapped in ssh
        self.assertEqual(cwd, str(remote.mount_dir("box")))
        self.assertTrue(env["PATH"].startswith(str(remote.bin_dir("box")) + ":"))
        self.assertEqual(env["X"], "1")
        self.assertNotIn("SSHPASS", env)  # the password stays with the master connection

    def test_local_projects_unchanged(self):
        p = ProjectInfo(name="l", path=Path("/tmp/projects/l"))
        argv, cwd, env = launch.build_command(p, ["codex"])
        self.assertEqual(cwd, "/tmp/projects/l")
        self.assertNotIn(".cache/oopsbox/remote", env["PATH"])

    def test_instructions(self):
        text = launch.memory_instructions(ssh_project(extra_dirs=["/data/shared"]))
        self.assertIn("ops@10.0.0.5", text)
        self.assertIn("`remote <command>`", text)
        self.assertIn("/tmp/projects/box/.memory/", text)  # the store, on this host
        self.assertIn("`/data/shared`", text)
        local = launch.memory_instructions(ProjectInfo(name="l", path=Path("/x")))
        self.assertNotIn("remote", local.lower().replace("remote folder", ""))
        self.assertNotIn("Other folders", local)


class TestRemoteCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.p = unittest.mock.patch.object(remote, "ROOT", self.d)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self._tmp.cleanup()

    def test_runs_in_the_remote_folder_over_the_master(self):
        remote._write_remote_command(ssh_project())
        fake = self.d / "fakebin"
        fake.mkdir()
        (fake / "ssh").write_text('#!/bin/sh\nfor a in "$@"; do printf "[%s]" "$a"; done\n')
        (fake / "ssh").chmod(0o755)
        out = subprocess.run([str(self.d / "box" / "bin" / "remote"), "make", "test"], capture_output=True,
                             text=True, env={**os.environ, "PATH": f"{fake}:/usr/bin:/bin"}).stdout
        self.assertIn("[-S][/tmp/oopsbox-box/ssh.sock]", out)
        self.assertIn("[BatchMode=yes]", out)
        self.assertTrue(out.endswith("[ops@10.0.0.5][cd '/srv/my app' && make test]"), out)

    def test_no_command_is_a_usage_error(self):
        remote._write_remote_command(ssh_project())
        r = subprocess.run([str(self.d / "box" / "bin" / "remote")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


class TestRun(unittest.TestCase):
    def test_a_command_that_leaves_a_background_child_returns(self):
        # like `ssh -f`: the command exits, a child it started keeps running
        code, out = asyncio.run(remote._run(["sh", "-c", "sleep 20 & echo started"], timeout=5))
        self.assertEqual((code, out), (0, "started"))


class TestEnsure(unittest.TestCase):
    def test_connects_then_mounts_once(self):
        calls = []
        state = {"master": False, "mounted": False}

        async def alive(p):
            return state["master"]

        async def start(p):
            calls.append("master")
            state["master"] = True

        async def mount(p):
            calls.append("mount")
            state["mounted"] = True

        with tempfile.TemporaryDirectory() as d, \
                unittest.mock.patch.object(remote, "ROOT", Path(d)), \
                unittest.mock.patch.multiple(remote, _master_alive=alive, _start_master=start, _mount=mount,
                                             _mounted=lambda path: state["mounted"]), \
                unittest.mock.patch.object(remote, "_healthy", side_effect=lambda path: asyncio.sleep(0, True)):
            asyncio.run(remote.ensure(ssh_project()))
            asyncio.run(remote.ensure(ssh_project()))
        self.assertEqual(calls, ["master", "mount"])

    def test_a_dropped_master_takes_the_mount_with_it(self):
        calls = []
        state = {"master": False, "mounted": True}

        async def unmount(path):
            calls.append("unmount")
            state["mounted"] = False

        async def start(p):
            calls.append("master")
            state["master"] = True

        async def mount(p):
            calls.append("mount")
            state["mounted"] = True

        with tempfile.TemporaryDirectory() as d, \
                unittest.mock.patch.object(remote, "ROOT", Path(d)), \
                unittest.mock.patch.multiple(remote, _master_alive=lambda p: asyncio.sleep(0, state["master"]),
                                             _start_master=start, _mount=mount, _unmount=unmount,
                                             _mounted=lambda path: state["mounted"]), \
                unittest.mock.patch.object(remote, "_healthy", side_effect=lambda path: asyncio.sleep(0, True)):
            asyncio.run(remote.ensure(ssh_project()))
        self.assertEqual(calls, ["unmount", "master", "mount"])


if __name__ == "__main__":
    unittest.main()
