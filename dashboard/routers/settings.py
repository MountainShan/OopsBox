# dashboard/routers/settings.py
import hashlib, hmac, json, subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from .auth import _auth_file, _hash_password, _verify_session
from fastapi import Request

router = APIRouter(prefix="/api/settings", tags=["settings"])

_settings_file = Path.home() / ".config/oopsbox/settings.json"


def _load_settings() -> dict:
    if not _settings_file.exists():
        return {}
    try:
        return json.loads(_settings_file.read_text())
    except Exception:
        return {}


def _save_settings(data: dict):
    _settings_file.parent.mkdir(parents=True, exist_ok=True)
    _settings_file.write_text(json.dumps(data, indent=2))


@router.get("")
def get_settings():
    auth = json.loads(_auth_file.read_text()) if _auth_file.exists() else {}
    s = _load_settings()
    api_key = s.get("api_key", "")
    masked_key = ("*" * (len(api_key) - 4) + api_key[-4:]) if len(api_key) > 4 else ("*" * len(api_key))
    return {
        "auth": {
            "username": auth.get("username", "admin"),
        },
        "agent": {
            "api_key": masked_key,
            "base_url": s.get("base_url", "https://api.anthropic.com"),
        },
        "git": {
            "name": s.get("git_name", ""),
            "email": s.get("git_email", ""),
        },
        "ssl": {
            "cert": s.get("ssl_cert", ""),
            "key": s.get("ssl_key", ""),
        },
    }


class AuthUpdateRequest(BaseModel):
    current_password: str
    new_username: Optional[str] = None
    new_password: Optional[str] = None


@router.put("/auth")
def update_auth(req: AuthUpdateRequest):
    if not _auth_file.exists():
        raise HTTPException(status_code=500, detail="Auth file not found")
    auth = json.loads(_auth_file.read_text())
    expected = _hash_password(req.current_password, auth["salt"])
    if not hmac.compare_digest(expected, auth["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password incorrect")

    import secrets
    if req.new_password:
        salt = secrets.token_hex(16)
        auth["salt"] = salt
        auth["password_hash"] = _hash_password(req.new_password, salt)
    if req.new_username:
        auth["username"] = req.new_username

    _auth_file.write_text(json.dumps(auth, indent=2))
    return {"ok": True}


class AgentUpdateRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None


@router.put("/agent")
def update_agent(req: AgentUpdateRequest):
    s = _load_settings()
    if req.api_key is not None and req.api_key and not req.api_key.startswith("*"):
        s["api_key"] = req.api_key
    if req.base_url is not None:
        s["base_url"] = req.base_url
    _save_settings(s)
    return {"ok": True}


class GitUpdateRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


@router.put("/git")
def update_git(req: GitUpdateRequest):
    s = _load_settings()
    if req.name is not None:
        s["git_name"] = req.name
        subprocess.run(["git", "config", "--global", "user.name", req.name], capture_output=True)
    if req.email is not None:
        s["git_email"] = req.email
        subprocess.run(["git", "config", "--global", "user.email", req.email], capture_output=True)
    _save_settings(s)
    return {"ok": True}


class SSLUpdateRequest(BaseModel):
    cert: Optional[str] = None
    key: Optional[str] = None


@router.put("/ssl")
def update_ssl(req: SSLUpdateRequest):
    s = _load_settings()
    if req.cert is not None:
        s["ssl_cert"] = req.cert
    if req.key is not None:
        s["ssl_key"] = req.key
    _save_settings(s)
    return {"ok": True}
