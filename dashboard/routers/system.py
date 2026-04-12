# dashboard/routers/system.py
import shutil
from fastapi import APIRouter
import psutil

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
