import hashlib, hmac, os, json, secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/auth", tags=["auth"])

_auth_file = Path.home() / ".config/oopsbox/auth.json"
_sessions_file = Path.home() / ".config/oopsbox/sessions.json"


def init_auth(auth_file: Path, sessions_file: Path):
    global _auth_file, _sessions_file
    _auth_file = auth_file
    _sessions_file = sessions_file


def _hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return dk.hex()


def _ensure_auth_file():
    if _auth_file.exists():
        return
    from dashboard.config import get_config
    cfg = get_config()
    _auth_file.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(16)
    data = {
        "username": cfg.username,
        "salt": salt,
        "password_hash": _hash_password(cfg.password or "", salt),
    }
    _auth_file.write_text(json.dumps(data, indent=2))
    _auth_file.chmod(0o600)


def _load_sessions() -> dict:
    if not _sessions_file.exists():
        return {}
    try:
        return json.loads(_sessions_file.read_text())
    except Exception:
        return {}


def _save_sessions(sessions: dict):
    _sessions_file.parent.mkdir(parents=True, exist_ok=True)
    _sessions_file.write_text(json.dumps(sessions, indent=2))
    _sessions_file.chmod(0o600)


def _verify_session(token: str) -> bool:
    sessions = _load_sessions()
    session = sessions.get(token)
    if not session:
        return False
    expires = datetime.fromisoformat(session["expires"])
    return datetime.now(timezone.utc) < expires


def require_auth(request: Request):
    token = request.cookies.get("session")
    if not token or not _verify_session(token):
        raise HTTPException(status_code=401, detail="Not authenticated")


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: LoginRequest, response: Response):
    _ensure_auth_file()
    auth = json.loads(_auth_file.read_text())

    if req.username != auth["username"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    expected = _hash_password(req.password, auth["salt"])
    if not hmac.compare_digest(expected, auth["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    sessions = _load_sessions()
    sessions[token] = {"username": req.username, "expires": expires.isoformat()}
    _save_sessions(sessions)

    response.set_cookie(key="session", value=token, httponly=True, samesite="lax", max_age=86400)
    return {"ok": True}


@router.get("/verify")
def verify(request: Request):
    token = request.cookies.get("session")
    if not token or not _verify_session(token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        sessions = _load_sessions()
        sessions.pop(token, None)
        _save_sessions(sessions)
    response.delete_cookie("session")
    return {"ok": True}


@router.get("/status")
def status(request: Request):
    token = request.cookies.get("session")
    authenticated = bool(token and _verify_session(token))
    return {"authenticated": authenticated}
