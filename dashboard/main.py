# dashboard/main.py
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .routers import auth, projects, files, system, ssh

app = FastAPI(title="OopsBox", version="2.0.0")

# Mount routers
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(files.router)
app.include_router(system.router)
app.include_router(ssh.router)

# Serve static files (HTML pages, CSS, JS)
_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


def _html(filename: str) -> FileResponse:
    path = _static / filename
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/login")
def login_page():
    return _html("login.html")


@app.get("/")
def index():
    return _html("index.html")


