# dashboard/main.py
import asyncio
import logging

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .agents.manager import AGENT_ORDER, manager
from . import notify, usage
from .routers import auth, projects, files, system, ssh, settings, chat

app = FastAPI(title="OopsBox", version="2.0.0")

# Mount routers
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(projects.shells)
app.include_router(files.router)
app.include_router(system.router)
app.include_router(ssh.router)
app.include_router(settings.router)
app.include_router(chat.router)


@app.on_event("startup")
async def _restore_agents():
    # In the background: a slow agent must not hold up the dashboard.
    asyncio.get_running_loop().create_task(projects.restore_agents())
    manager.attention_hook = notify.attention
    asyncio.get_running_loop().create_task(_watch_usage())


async def _watch_usage():
    """Every 10 minutes, push an alert for any agent limit past the threshold."""
    await asyncio.sleep(120)  # let the restored agents settle
    while True:
        try:
            agents = await usage.agent_usage(manager, AGENT_ORDER)
            for title, body in notify.usage_alerts(agents):
                await notify.send(title, body, url="/", tag="usage-" + title.split()[0])
        except Exception:
            logging.getLogger(__name__).exception("usage watch failed")
        await asyncio.sleep(600)


@app.on_event("shutdown")
async def _stop_agents():
    # Agents are children of this process; leaving them would orphan them
    # on every restart.
    await manager.shutdown()

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


# The service worker is served from the root so its scope is the whole
# app: push subscriptions and the installed iOS app need a worker that
# controls the page, and one under /static/ controls nothing above it.
@app.get("/sw.js")
def service_worker():
    return FileResponse(_static / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


