import sys, os, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from fastapi import FastAPI
from dashboard.routers.files import router, set_projects_root

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def setup_project(tmp_path):
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    (project_dir / "hello.txt").write_text("hello world")
    (project_dir / "subdir").mkdir()
    set_projects_root(tmp_path)
    return project_dir

def test_list_files(tmp_path):
    setup_project(tmp_path)
    r = client.get("/api/files/myproject")
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["files"]]
    assert "hello.txt" in names
    assert "subdir" in names

def test_read_file(tmp_path):
    setup_project(tmp_path)
    r = client.get("/api/files/myproject/read", params={"path": "hello.txt"})
    assert r.status_code == 200
    assert r.json()["content"] == "hello world"

def test_rename_file(tmp_path):
    setup_project(tmp_path)
    r = client.post("/api/files/myproject/rename",
                    json={"path": "hello.txt", "new_name": "renamed.txt"})
    assert r.status_code == 200
    assert (tmp_path / "myproject" / "renamed.txt").exists()
    assert not (tmp_path / "myproject" / "hello.txt").exists()

def test_delete_file(tmp_path):
    setup_project(tmp_path)
    r = client.post("/api/files/myproject/delete", json={"path": "hello.txt"})
    assert r.status_code == 200
    assert not (tmp_path / "myproject" / "hello.txt").exists()

def test_mkdir(tmp_path):
    setup_project(tmp_path)
    r = client.post("/api/files/myproject/mkdir", json={"path": "newdir"})
    assert r.status_code == 200
    assert (tmp_path / "myproject" / "newdir").is_dir()

def test_path_traversal_blocked(tmp_path):
    setup_project(tmp_path)
    r = client.get("/api/files/myproject/read", params={"path": "../../etc/passwd"})
    assert r.status_code == 400
