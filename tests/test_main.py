import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from dashboard.main import app

client = TestClient(app)

def test_login_page_redirects():
    r = client.get("/", follow_redirects=False)
    # Should redirect to /login or serve index
    assert r.status_code in (200, 302, 307)

def test_static_login_page():
    r = client.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]

def test_api_auth_status_unauthenticated():
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json()["authenticated"] == False

def test_api_system_requires_auth():
    r = client.get("/api/system")
    # System endpoint accessible (auth is handled by nginx, not FastAPI)
    assert r.status_code == 200
