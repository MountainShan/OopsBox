# tests/test_system.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from fastapi import FastAPI
from dashboard.routers.system import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)

def test_system_stats_shape():
    r = client.get("/api/system")
    assert r.status_code == 200
    data = r.json()
    assert "cpu_percent" in data
    assert "ram" in data
    assert "disk" in data
    assert "used" in data["ram"]
    assert "total" in data["ram"]
    assert "used" in data["disk"]
    assert "total" in data["disk"]

def test_system_values_are_numbers():
    r = client.get("/api/system")
    data = r.json()
    assert isinstance(data["cpu_percent"], (int, float))
    assert isinstance(data["ram"]["used"], (int, float))
