"""Agent settings files, edited in place."""
import os
import tempfile
import unittest
from pathlib import Path

from dashboard.agents import config_files as cf


class TestConfigFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.proj = self.root / "proj"
        self.proj.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_scopes_per_agent(self):
        self.assertEqual([f.scope for f in cf.config_files("claude", self.proj, self.home)],
                         ["user", "project", "local"])
        self.assertEqual(cf.find("codex", self.proj, "user", self.home).path,
                         self.home / ".codex/config.toml")
        self.assertEqual(cf.find("kiro", self.proj, "project-mcp", self.home).path,
                         self.proj / ".kiro/settings/mcp.json")

    def test_invalid_json_not_written(self):
        f = cf.find("claude", self.proj, "project", self.home)
        cf.write_config(f, '{"a": 1}')
        with self.assertRaises(ValueError):
            cf.write_config(f, '{"a": ')
        self.assertEqual(cf.read_config(f), '{"a": 1}')

    def test_toml_validated(self):
        f = cf.find("codex", self.proj, "user", self.home)
        with self.assertRaises(ValueError):
            cf.write_config(f, 'model = ')
        cf.write_config(f, 'model = "gpt"\n')
        self.assertEqual(cf.read_config(f), 'model = "gpt"\n')

    def test_backup_made(self):
        f = cf.find("kiro", self.proj, "user", self.home)
        cf.write_config(f, '{"v": 1}')
        cf.write_config(f, '{"v": 2}')
        bak = f.path.with_name(f.path.name + ".oopsbox-bak")
        self.assertEqual(bak.read_text(), '{"v": 1}')

    def test_symlink_target_written(self):
        real = self.root / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        link = self.home / ".claude" / "settings.json"
        link.parent.mkdir(parents=True)
        os.symlink(real, link)
        cf.write_config(cf.find("claude", self.proj, "user", self.home), '{"x": true}')
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), '{"x": true}')

    def test_missing_file_reads_empty(self):
        self.assertEqual(cf.read_config(cf.find("claude", self.proj, "local", self.home)), "")


if __name__ == "__main__":
    unittest.main()
