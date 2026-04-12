# tests/test_projects.py
import sys, json, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from fastapi import FastAPI
from dashboard.routers.projects import router, set_projects_root

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def setup(tmp_path):
    set_projects_root(tmp_path)
    return tmp_path

def test_list_empty(tmp_path):
    setup(tmp_path)
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert r.json() == []

def test_create_local(tmp_path):
    setup(tmp_path)
    r = client.post("/api/projects", json={"name": "my-app", "type": "local"})
    assert r.status_code == 200
    assert (tmp_path / "my-app").is_dir()
    assert (tmp_path / "my-app" / "CLAUDE.md").exists()

def test_create_duplicate(tmp_path):
    setup(tmp_path)
    client.post("/api/projects", json={"name": "my-app", "type": "local"})
    r = client.post("/api/projects", json={"name": "my-app", "type": "local"})
    assert r.status_code == 400

def test_list_after_create(tmp_path):
    setup(tmp_path)
    client.post("/api/projects", json={"name": "my-app", "type": "local"})
    r = client.get("/api/projects")
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "my-app"

def test_get_project(tmp_path):
    setup(tmp_path)
    client.post("/api/projects", json={"name": "my-app", "type": "local"})
    r = client.get("/api/projects/my-app")
    assert r.status_code == 200
    assert r.json()["name"] == "my-app"
    assert r.json()["type"] == "local"

def test_delete_project(tmp_path):
    setup(tmp_path)
    client.post("/api/projects", json={"name": "my-app", "type": "local"})
    r = client.delete("/api/projects/my-app")
    assert r.status_code == 200
    assert not (tmp_path / "my-app").exists()
    r2 = client.get("/api/projects")
    assert r2.json() == []

def test_invalid_name(tmp_path):
    setup(tmp_path)
    r = client.post("/api/projects", json={"name": "my app!", "type": "local"})
    assert r.status_code == 422
