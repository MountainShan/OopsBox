"""Which agents a machine has: the detection script and its cache."""
import asyncio
import os
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from dashboard.agents import catalog


class TestDetect(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)
        self.p = unittest.mock.patch.object(catalog, "_cache_file", self.d / "detected.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self._tmp.cleanup()

    def test_script_finds_what_is_on_the_path(self):
        bin_ = self.d / "bin"
        bin_.mkdir()
        fake = bin_ / "codex"
        fake.write_text("#!/bin/sh\necho 'codex-cli 9.9.9'\necho more\n")
        fake.chmod(0o755)
        out = subprocess.run(["bash", "-c", catalog._script()], capture_output=True, text=True,
                             env={**os.environ, "PATH": f"{bin_}:/usr/bin:/bin"}).stdout
        found = catalog.parse(out)
        self.assertEqual(found["codex"], {"state": "installed", "path": str(fake), "version": "codex-cli 9.9.9"})
        self.assertEqual(found["kiro"], {"state": "missing"})

    def test_cut_short_is_unknown_not_missing(self):
        found = catalog.parse("codex\t/usr/bin/codex\tv1\n")
        self.assertEqual(found["claude"], {"state": "unknown"})

    def test_host_keys(self):
        self.assertEqual(catalog.host_key({"type": "local"}), "local")
        self.assertEqual(catalog.host_key({"type": "ssh", "ssh_user": "ops", "ssh_host": "10.0.0.2"}), "ops@10.0.0.2:22")

    def test_detect_remembers_each_machine_once(self):
        registry = {
            "a": {"name": "a", "type": "ssh", "ssh_user": "ops", "ssh_host": "h", "ssh_port": 22, "path": "/x"},
            "b": {"name": "b", "type": "ssh", "ssh_user": "ops", "ssh_host": "h", "ssh_port": 22, "path": "/y"},
            "c": {"name": "c", "type": "local", "path": "/z"},
        }
        seen = []
        async def probe(meta):
            seen.append(catalog.host_key(meta))
            return {"at": 1, "agents": catalog.parse("kiro\t/k\tkiro 2\n")}
        with unittest.mock.patch.object(catalog, "probe", probe):
            data = asyncio.run(catalog.detect(registry))
        self.assertEqual(sorted(seen), ["local", "ops@h:22"])
        self.assertEqual(data["ops@h:22"]["agents"]["kiro"]["state"], "installed")
        self.assertTrue(catalog.installed(registry["a"], "kiro"))
        self.assertIsNone(catalog.installed(registry["a"], "codex"))  # unknown
        # a machine no project uses any more is forgotten
        with unittest.mock.patch.object(catalog, "probe", probe):
            data = asyncio.run(catalog.detect({"c": registry["c"]}))
        self.assertEqual(list(data), ["local"])

    def test_unreachable_host_says_why(self):
        meta = {"name": "a", "type": "ssh", "ssh_user": "ops", "ssh_host": "h", "path": "/x"}
        with unittest.mock.patch.object(catalog, "build_command",
                                        return_value=(["sh", "-c", "echo 'ssh: connect: refused' >&2; exit 255"], None, dict(os.environ))):
            r = asyncio.run(catalog.probe(meta))
        self.assertEqual(r["error"], "ssh: connect: refused")
        self.assertTrue(all(a["state"] == "unknown" for a in r["agents"].values()))


if __name__ == "__main__":
    unittest.main()
