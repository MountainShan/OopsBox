import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from dashboard.main import app

client = TestClient(app)

def test_root_serves_dashboard():
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200

def test_static_login_page():
    r = client.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"<html" in r.content

def test_api_auth_status_unauthenticated():
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["authenticated"] == False

def test_api_system_accessible_without_auth():
    # Auth is handled by nginx, not FastAPI — system is always accessible at the app level
    r = client.get("/api/system")
    assert r.status_code == 200
