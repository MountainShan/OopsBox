# dashboard/routers/system.py
import asyncio
import json
import shutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import psutil

from .. import notify, usage
from ..agents.catalog import STABLE
from ..agents.manager import manager

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system")
def get_system():
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = shutil.disk_usage("/")

    return {
        "cpu_percent": cpu,
        "ram": {
            "used": ram.used,
            "total": ram.total,
            "percent": ram.percent,
        },
        "swap": {
            "used": swap.used,
            "total": swap.total,
            "percent": swap.percent,
        },
        "disk": {
            "used": disk.used,
            "total": disk.total,
            "percent": round(disk.used / disk.total * 100, 1),
        },
    }


@router.get("/overview")
async def get_overview():
    """The overview page: the machine, and each agent's account usage."""
    machine, agents = await asyncio.gather(asyncio.to_thread(usage.system),
                                           usage.agent_usage(manager, STABLE))
    return {"system": machine, "agents": agents, "alert_pct": notify.alert_pct(),
            "waiting": manager.waiting()}


# ── Notifications ────────────────────────────────────
@router.get("/notify")
def notify_info():
    """What the Settings page needs to turn push on in this browser."""
    return {"key": notify.public_key(), "subscriptions": len(notify.subscriptions()),
            "usage_alert_pct": notify.alert_pct()}


@router.post("/notify/subscribe")
def notify_subscribe(sub: dict):
    try:
        notify.subscribe(sub)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


class Unsubscribe(BaseModel):
    endpoint: str


@router.post("/notify/unsubscribe")
def notify_unsubscribe(req: Unsubscribe):
    notify.unsubscribe(req.endpoint)
    return {"ok": True}


@router.post("/notify/test")
async def notify_test():
    sent = await notify.send("OopsBox", "Notifications are on for this device.", tag="test")
    return {"sent": sent}


class AlertSettings(BaseModel):
    usage_alert_pct: int


@router.put("/notify/settings")
def notify_settings(req: AlertSettings):
    if not 1 <= req.usage_alert_pct <= 1000:
        raise HTTPException(status_code=400, detail="usage_alert_pct must be 1–1000")
    f = notify._settings_file
    data = notify._load(f, {})
    data["usage_alert_pct"] = req.usage_alert_pct
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2))
    return {"ok": True}
