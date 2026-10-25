# dashboard/notify.py
"""Telling people when something needs them, even with OopsBox closed.

Browsers that turned notifications on (Settings → Notifications) are sent
a Web Push message: when an agent stops to ask for a permission or an
answer, and when an agent's usage passes the alert threshold. Push needs
the page served over HTTPS (on the tailnet: tailscale serve) and, on an
iPad or iPhone, OopsBox added to the home screen.

The VAPID key pair that signs the messages is made on first use and kept
in ~/.config/oopsbox, next to the subscriptions.
"""
import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CONFIG = Path.home() / ".config" / "oopsbox"
_key_file = CONFIG / "vapid-private.pem"
_subs_file = CONFIG / "push-subscriptions.json"
_alerts_file = CONFIG / "usage-alerts.json"
_settings_file = CONFIG / "settings.json"

DEFAULT_ALERT_PCT = 90
# Push services want a contact: an https URL of our own (py_vapid takes
# none with a port). Each browser is signed for with the address it
# subscribed on; this is for the ones that did not say.
SUBJECT = os.environ.get("OOPSBOX_PUSH_SUBJECT", "mailto:oopsbox@example.com")


# ── Keys and subscriptions ─────────────────────────
def _vapid():
    from py_vapid import Vapid
    if not _key_file.exists():
        v = Vapid()
        v.generate_keys()
        CONFIG.mkdir(parents=True, exist_ok=True)
        _key_file.write_bytes(v.private_pem())
        _key_file.chmod(0o600)
    return Vapid.from_file(str(_key_file))


def public_key() -> str:
    """The application server key a browser subscribes with (base64url)."""
    from cryptography.hazmat.primitives import serialization
    raw = _vapid().public_key.public_bytes(serialization.Encoding.X962,
                                           serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.chmod(0o600)
    tmp.replace(path)


def subscriptions() -> list[dict]:
    return _load(_subs_file, [])


def subject_for(origin: Optional[str]) -> Optional[str]:
    """The VAPID subject for a page at this origin: its https URL, port dropped."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(origin or "")
    except ValueError:
        return None
    return f"https://{u.hostname}" if u.scheme == "https" and u.hostname else None


def subscribe(sub: dict) -> None:
    if not isinstance(sub, dict) or not str(sub.get("endpoint", "")).startswith("https://") \
            or not isinstance(sub.get("keys"), dict):
        raise ValueError("not a push subscription")
    subs = [s for s in subscriptions() if s.get("endpoint") != sub["endpoint"]]
    entry = {"endpoint": sub["endpoint"], "keys": sub["keys"]}
    if subject := subject_for(sub.get("origin")):
        entry["subject"] = subject
    _save(_subs_file, subs + [entry])


def unsubscribe(endpoint: str) -> None:
    _save(_subs_file, [s for s in subscriptions() if s.get("endpoint") != endpoint])


# ── Sending ────────────────────────────────────────
def _send_one(sub: dict, payload: str) -> Optional[int]:
    """Send to one browser; the HTTP status of a failure, else None."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]}, data=payload,
                vapid_private_key=str(_key_file), vapid_claims={"sub": sub.get("subject") or SUBJECT},
                ttl=3600)
        return None
    except WebPushException as e:
        return getattr(e.response, "status_code", None) or 0


async def send(title: str, body: str, url: str = "/", tag: Optional[str] = None) -> int:
    """Push to every subscribed browser. Returns how many took it."""
    subs = subscriptions()
    if not subs:
        return 0
    _vapid()  # make sure the key exists
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or url})
    results = await asyncio.gather(*(asyncio.to_thread(_send_one, s, payload) for s in subs),
                                   return_exceptions=True)
    gone = [s["endpoint"] for s, r in zip(subs, results) if r in (404, 410)]
    for endpoint in gone:  # the browser dropped the subscription
        unsubscribe(endpoint)
    failed = [r for r in results if r not in (None, 404, 410)]
    if failed:
        log.warning("push failed for %d of %d browsers: %s", len(failed), len(subs), failed)
    return sum(1 for r in results if r is None)


def attention(project: str, agent: str, kind: str, text: str) -> None:
    """An agent waits for the user: tell them (fire and forget)."""
    what = "needs your permission" if kind == "permission" else "has a question"
    asyncio.get_running_loop().create_task(send(
        f"{agent} {what}", f"{project}: {text}"[:180],
        url=f"/?open={project}&agent={agent}", tag=f"{project}/{agent}"))


# ── Usage alerts ───────────────────────────────────
def alert_pct() -> int:
    s = _load(_settings_file, {})
    try:
        return int(s.get("usage_alert_pct") or DEFAULT_ALERT_PCT)
    except (TypeError, ValueError):
        return DEFAULT_ALERT_PCT


def usage_alerts(agents: dict, threshold: Optional[int] = None) -> list[tuple[str, str]]:
    """The (title, body) alerts due for this usage, each limit once per
    reset period; remembers the ones it returns."""
    threshold = threshold or alert_pct()
    seen = _load(_alerts_file, {})
    due = []
    for agent, a in agents.items():
        for l in a.get("limits") or []:
            if (l.get("percent") or 0) < threshold:
                continue
            key = f"{agent}|{l.get('label')}|{l.get('resets_at')}"
            if key in seen:
                continue
            seen[key] = time.time()
            due.append((f"{agent} usage at {round(l['percent'])}%",
                        f"{l.get('label')} limit" + (f", resets {l['resets_at']}" if l.get("resets_at") else "")))
    if due:
        # forget alerts older than 60 days
        cutoff = time.time() - 60 * 86400
        _save(_alerts_file, {k: v for k, v in seen.items() if v > cutoff})
    return due
