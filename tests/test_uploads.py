"""Uploads: safe names, free names, the size limit, and where files land."""
import asyncio
import io
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from fastapi import HTTPException, UploadFile

from dashboard import uploads


def upload(data: bytes, name: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=name)


class TestNames(unittest.TestCase):
    def test_paths_are_dropped(self):
        self.assertEqual(uploads.safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(uploads.safe_name("a\\b\\c.txt"), "c.txt")
        self.assertEqual(uploads.safe_name(".."), "file")
        self.assertEqual(uploads.safe_name("bad\x00name.txt"), "badname.txt")

    def test_free_name(self):
        taken = {"a.txt", "a-1.txt"}
        self.assertEqual(uploads.unique_name("a.txt", taken.__contains__), "a-2.txt")
        self.assertEqual(uploads.unique_name("README", {"README"}.__contains__), "README-1")

    def test_dir_must_stay_inside(self):
        self.assertEqual(uploads.check_dir("/uploads/chat/"), "uploads/chat")
        for bad in ("../x", "a/../../b", "~/x", ""):
            with self.assertRaises(ValueError):
                uploads.check_dir(bad)


class TestSave(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.settings = self.root / "settings.json"
        self.patch = unittest.mock.patch.object(uploads, "_settings_file", self.settings)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self._tmp.cleanup()

    async def test_saved_under_a_free_name(self):
        d = self.root / "proj" / "uploads"
        p1, n1 = await uploads.save_to_dir(upload(b"x" * 10, "r.pdf"), d)
        p2, _ = await uploads.save_to_dir(upload(b"y", "r.pdf"), d)
        self.assertEqual((p1.name, n1, p2.name), ("r.pdf", 10, "r-1.pdf"))
        self.assertEqual(p2.read_bytes(), b"y")

    async def test_limit_stops_and_cleans_up(self):
        self.settings.write_text(json.dumps({"upload_max_mb": 1}))
        d = self.root / "proj"
        with self.assertRaises(HTTPException) as cm:
            await uploads.save_to_dir(upload(b"x" * (1024 * 1024 + 1), "big.bin"), d)
        self.assertEqual(cm.exception.status_code, 413)
        self.assertEqual(list(d.iterdir()), [])

    async def test_files_tab_cannot_escape_the_folder(self):
        from dashboard.routers import files
        (self.root / "projects" / "p").mkdir(parents=True)
        with unittest.mock.patch.object(files, "_projects_root", self.root / "projects"):
            res = await files.upload("p", "", upload(b"hi", "../../evil.txt"))
        self.assertEqual(res["name"], "evil.txt")
        self.assertTrue((self.root / "projects" / "p" / "evil.txt").exists())
        self.assertFalse((self.root / "evil.txt").exists())

    async def test_chat_attachment_lands_in_the_project(self):
        from dashboard.routers import chat
        proj = self.root / "proj"
        proj.mkdir()
        meta = {"name": "proj", "path": str(proj), "type": "local"}
        with unittest.mock.patch.object(chat, "_meta", return_value=meta), \
             unittest.mock.patch.object(chat, "_session", return_value=None):
            res = await chat.chat_attach("proj", "claude", upload(b"\x89PNG", "shot.png"))
        self.assertEqual(res["path"], "uploads/shot.png")
        self.assertEqual(res["mime"], "image/png")
        files, inline = chat._attached_files(meta, [res["path"]], want_images=True)
        self.assertEqual(files[0]["size"], 4)
        self.assertEqual(len(inline), 1)
        with self.assertRaises(HTTPException):
            chat._attached_files(meta, ["../outside.txt"], want_images=True)


class TestNote(unittest.TestCase):
    def test_agent_is_told_where_files_are(self):
        from dashboard.agents.session import files_note
        note = files_note([{"path": "uploads/a.pdf", "size": 3 * 1048576}, {"path": "uploads/b.txt", "size": 10}])
        self.assertIn("uploads/a.pdf (3.0 MB)", note)
        self.assertIn("uploads/b.txt (1 KB)", note)


if __name__ == "__main__":
    unittest.main()
