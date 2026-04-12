# dashboard/main.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pathlib import Path

from .routers import auth, projects, files, system

app = FastAPI(title="OopsBox", version="2.0.0")

# Mount routers
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(files.router)
app.include_router(system.router)

# Serve static files (HTML pages, CSS, JS)
_static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_static), name="static")


@app.get("/login")
def login_page():
    from fastapi.responses import HTMLResponse
    return HTMLResponse((_static / "login.html").read_text())


@app.get("/")
def index():
    from fastapi.responses import HTMLResponse
    return HTMLResponse((_static / "index.html").read_text())


@app.get("/workspace")
def workspace():
    from fastapi.responses import HTMLResponse
    return HTMLResponse((_static / "workspace.html").read_text())
