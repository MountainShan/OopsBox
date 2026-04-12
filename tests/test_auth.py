import sys, os, json, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["OOPSBOX_USERNAME"] = "admin"
os.environ["OOPSBOX_PASSWORD"] = "testpass"

import dashboard.config as cfg
cfg.reset_config()

from fastapi.testclient import TestClient
from fastapi import FastAPI
from dashboard.routers.auth import router, init_auth

app = FastAPI()
app.include_router(router)

def make_client(tmp_path):
    init_auth(
        auth_file=tmp_path / "auth.json",
        sessions_file=tmp_path / "sessions.json"
    )
    return TestClient(app, raise_server_exceptions=True)

def test_login_success(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    assert r.status_code == 200
    assert "session" in r.cookies

def test_login_wrong_password(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401

def test_verify_with_cookie(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    r = client.get("/api/auth/verify")
    assert r.status_code == 200

def test_verify_without_cookie(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/api/auth/verify")
    assert r.status_code == 401

def test_logout(tmp_path):
    client = make_client(tmp_path)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    client.post("/api/auth/logout")
    r = client.get("/api/auth/verify")
    assert r.status_code == 401
