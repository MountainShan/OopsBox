# dashboard/usage.py
"""What the overview page shows: each agent's account usage and the
machine's load.

Agent usage is account-wide, so any running session of an agent can
answer for it; each is asked its own /usage (the same answer as in its
terminal) and the answer is reduced to a plan and a few limit bars. An
agent nobody is running is not started just to be asked: its last answer
is shown, marked as old, or nothing. Answers are kept for a minute so a
page left open does not keep asking.
"""
import asyncio
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional

import psutil

TTL = 60
_cache: dict[str, dict] = {}  # agent -> {"at": time, "usage": {...}}


def _iso(ts) -> Optional[str]:
    """resets_at as ISO 8601, from either an epoch number or a string."""
    if ts in (None, ""):
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return str(ts)


def _window(mins) -> str:
    if not mins:
        return "Limit"
    if mins == 10080:
        return "Weekly"
    if mins % 1440 == 0:
        return f"{mins // 1440}d"
    if mins % 60 == 0:
        return f"{mins // 60}h"
    return f"{mins}m"


_CLAUDE_KIND = {"session": "5h", "weekly_all": "Weekly (all models)"}


def from_claude(d: dict) -> dict:
    limits = []
    for l in d.get("limits") or []:
        label = _CLAUDE_KIND.get(l.get("kind"))
        if label is None:
            model = (((l.get("scope") or {}).get("model") or {}).get("display_name"))
            label = f"Weekly · {model}" if model else (l.get("kind") or "Limit")
        limits.append({"label": label, "percent": l.get("percent") or 0, "resets_at": _iso(l.get("resets_at"))})
    if not limits:  # older CLIs: only the raw windows
        for key, label in (("five_hour", "5h"), ("seven_day", "Weekly")):
            w = (d.get("rate_limits") or {}).get(key)
            if w:
                limits.append({"label": label, "percent": w.get("utilization") or 0, "resets_at": _iso(w.get("resets_at"))})
    notes = []
    extra = d.get("extra_usage") or {}
    if extra.get("is_enabled"):
        notes.append(f"Extra usage {extra.get('used_credits', 0) / 100:.2f} / {extra.get('monthly_limit', 0) / 100:.0f} {extra.get('currency', 'USD')}")
    return {"plan": d.get("subscription_type"), "limits": limits, "notes": notes}


def from_codex(d: dict) -> dict:
    rl = d.get("rate_limits") or {}
    limits = []
    for key in ("primary", "secondary"):
        w = rl.get(key)
        if w:
            limits.append({"label": _window(w.get("windowDurationMins")), "percent": w.get("usedPercent") or 0,
                           "resets_at": _iso(w.get("resetsAt"))})
    notes = []
    credits = rl.get("credits") or {}
    if credits.get("unlimited"):
        notes.append("Credits: unlimited")
    elif credits.get("balance") not in (None, ""):
        notes.append(f"Credits balance {float(credits['balance']):,.0f}")
    daily = d.get("daily") or []
    if daily:
        notes.append(f"{daily[-1].get('startDate')}: {daily[-1].get('tokens', 0) / 1e6:,.1f}M tokens")
    return {"plan": rl.get("planType"), "limits": limits, "notes": notes}


def from_kiro(d: dict) -> dict:
    limits, notes = [], []
    for b in d.get("usageBreakdowns") or []:
        limits.append({"label": b.get("displayName") or b.get("resourceType") or "Limit",
                       "percent": b.get("percentage") or 0,
                       "detail": f"{b.get('used', 0):,.0f} / {b.get('limit', 0):,.0f}" if b.get("hasLimit") else None,
                       "resets_at": d.get("billingCycleReset")})
        if b.get("overageCharges"):
            notes.append(f"Overage {b.get('currentOverages', 0):,.0f}, charges {b['overageCharges']:.2f} {b.get('currency', 'USD')}")
    return {"plan": d.get("planName"), "limits": limits, "notes": notes}


NORMALIZE = {"claude": from_claude, "codex": from_codex, "kiro": from_kiro}


async def agent_usage(manager, agents: list[str]) -> dict:
    """{agent: {state, plan, limits, notes, at, running_in}} for the overview."""
    async def one(agent: str) -> tuple[str, dict]:
        running_in = manager.projects_running(agent)
        cached = _cache.get(agent)
        if cached and time.time() - cached["at"] < TTL:
            return agent, {**cached["usage"], "state": "ok", "at": cached["at"], "running_in": running_in}
        session = manager.any_alive(agent)
        if session is None:
            if cached:
                return agent, {**cached["usage"], "state": "stale", "at": cached["at"], "running_in": running_in}
            return agent, {"state": "stopped", "running_in": running_in}
        try:
            data = await asyncio.wait_for(session.usage_data(), 20)
            usage = NORMALIZE[agent](data or {})
        except Exception as e:
            out = {**cached["usage"], "at": cached["at"]} if cached else {}
            return agent, {**out, "state": "error", "error": str(e) or e.__class__.__name__, "running_in": running_in}
        _cache[agent] = {"at": time.time(), "usage": usage}
        return agent, {**usage, "state": "ok", "at": _cache[agent]["at"], "running_in": running_in}

    return dict(await asyncio.gather(*(one(a) for a in agents)))


def _gpus() -> list[dict] | dict:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": str(e)}
    if r.returncode != 0:
        return {"error": (r.stdout or r.stderr).strip().splitlines()[0] if (r.stdout or r.stderr).strip() else "nvidia-smi failed"}
    gpus = []
    for line in r.stdout.strip().splitlines():
        name, util, used, total, temp = [x.strip() for x in line.split(",")]
        gpus.append({"name": name, "percent": float(util), "mem_used": float(used) * 2**20,
                     "mem_total": float(total) * 2**20, "temp": float(temp)})
    return gpus


def system() -> dict:
    ram = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    data = shutil.disk_usage("/data") if os.path.isdir("/data") and os.path.ismount("/data") else None
    gpus = _gpus()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "cpus": psutil.cpu_count(),
        "load": list(os.getloadavg()),
        "uptime": time.time() - psutil.boot_time(),
        "ram": {"used": ram.used, "total": ram.total, "percent": ram.percent},
        "disks": [{"path": p, "used": d.used, "total": d.total, "percent": round(d.used / d.total * 100, 1)}
                  for p, d in (("/", disk), ("/data", data)) if d],
        "gpus": gpus if isinstance(gpus, list) else [],
        "gpu_error": gpus.get("error") if isinstance(gpus, dict) else None,
    }
