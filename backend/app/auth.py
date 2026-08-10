"""User accounts, session cookies, and per-user performance tracking."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse


_workspace_root = os.getenv("PROTOLABEL_WORKSPACE_ROOT")
ROOT = Path(_workspace_root).resolve() if _workspace_root else Path(__file__).resolve().parents[3]
DATA = Path(os.getenv("PROTOLABEL_DATA_DIR", ROOT / "data")).resolve()
DB = DATA / "prot0label.sqlite3"
SESSION_COOKIE = "protolabel_session"
SESSION_TTL = int(os.getenv("PROTOLABEL_SESSION_TTL_SECONDS", str(7 * 86400)))
COOKIE_SECURE = os.getenv("PROTOLABEL_COOKIE_SECURE", "false").lower() == "true"
PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/register"}
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")
router = APIRouter()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    return salt.hex(), _password_hash(password, salt).hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        actual = _password_hash(password, bytes.fromhex(salt_hex)).hex()
        return secrets.compare_digest(actual, expected_hex)
    except (ValueError, TypeError):
        return False


def init_auth_schema() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    connection = db()
    try:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          display_name TEXT NOT NULL, password_salt TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'annotator',
          active INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions(
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
          created_at REAL NOT NULL, expires_at REAL NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
        CREATE TABLE IF NOT EXISTS user_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
          event_type TEXT NOT NULL, project_id TEXT, image_id TEXT,
          value INTEGER NOT NULL DEFAULT 0, elapsed_seconds REAL NOT NULL DEFAULT 0,
          created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS user_events_user_time ON user_events(user_id,created_at);
        CREATE TABLE IF NOT EXISTS image_activity(
          user_id TEXT NOT NULL, image_id TEXT NOT NULL, opened_at REAL NOT NULL,
          PRIMARY KEY(user_id,image_id)
        );
        CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO app_settings(key,value) VALUES('registration_enabled','1');
        """)
        if connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            salt, password_hash = hash_password("admin")
            now = time.time()
            connection.execute(
                """INSERT INTO users(id,username,display_name,password_salt,password_hash,
                   role,active,must_change_password,created_at,updated_at)
                   VALUES(?,?,?,?,?,'admin',1,1,?,?)""",
                (uuid.uuid4().hex, "admin", "Administrator", salt, password_hash, now, now),
            )
        connection.execute("DELETE FROM sessions WHERE expires_at<=?", (time.time(),))
        connection.commit()
    finally:
        connection.close()


async def authenticate(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    token = request.cookies.get(SESSION_COOKIE, "")
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else ""
    connection = db()
    user = connection.execute(
        """SELECT u.id,u.username,u.display_name,u.role,u.must_change_password
           FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""",
        (token_hash, time.time()),
    ).fetchone()
    connection.close()
    if not user:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Invalid request origin"}, status_code=403)
    request.state.user = dict(user)
    allowed_while_changing = {"/api/auth/me", "/api/auth/change-password", "/api/auth/logout"}
    if user["must_change_password"] and request.url.path not in allowed_while_changing:
        return JSONResponse({"detail": "Password change required", "code": "password_change_required"}, status_code=403)
    return await call_next(request)


def _validate_new_password(password: Any) -> str:
    value = str(password or "")
    if len(value) < 10 or len(value) > 128:
        raise HTTPException(422, "Password must contain 10-128 characters")
    return value


def _user_dict(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value.pop("password_salt", None)
    value.pop("password_hash", None)
    value.pop("updated_at", None)
    value["active"] = bool(value.get("active", 1))
    value["must_change_password"] = bool(value.get("must_change_password", 0))
    return value


def _create_session(connection: sqlite3.Connection, user_id: str) -> tuple[str, float]:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL
    connection.execute(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time(), expires_at),
    )
    return token, expires_at


def _set_session_cookie(response: JSONResponse, token: str, expires_at: float) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=max(0, int(expires_at - time.time())),
        httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/",
    )


@router.post("/api/auth/register")
def register(payload: dict[str, Any]):
    username = str(payload.get("username", "")).strip()
    display_name = str(payload.get("display_name") or username).strip()[:80]
    password = _validate_new_password(payload.get("password"))
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(422, "Username must be 3-32 letters, numbers, dot, dash or underscore")
    connection = db()
    enabled = connection.execute("SELECT value FROM app_settings WHERE key='registration_enabled'").fetchone()
    if not enabled or enabled["value"] != "1":
        connection.close()
        raise HTTPException(403, "Registration is disabled")
    salt, password_hash = hash_password(password)
    now, user_id = time.time(), uuid.uuid4().hex
    try:
        connection.execute(
            """INSERT INTO users(id,username,display_name,password_salt,password_hash,
               role,active,must_change_password,created_at,updated_at)
               VALUES(?,?,?,?,?,'annotator',1,0,?,?)""",
            (user_id, username, display_name, salt, password_hash, now, now),
        )
        token, expires_at = _create_session(connection, user_id)
        connection.commit()
    except sqlite3.IntegrityError:
        connection.close()
        raise HTTPException(409, "Username already exists")
    connection.close()
    response = JSONResponse({"user": {"id": user_id, "username": username, "display_name": display_name, "role": "annotator", "must_change_password": False}})
    _set_session_cookie(response, token, expires_at)
    return response


@router.post("/api/auth/login")
def login(payload: dict[str, Any]):
    username, password = str(payload.get("username", "")).strip(), str(payload.get("password", ""))
    connection = db()
    row = connection.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (username,)).fetchone()
    if not row or not verify_password(password, row["password_salt"], row["password_hash"]):
        connection.close()
        raise HTTPException(401, "Invalid username or password")
    token, expires_at = _create_session(connection, row["id"])
    connection.execute("INSERT INTO user_events(user_id,event_type,created_at) VALUES(?,?,?)", (row["id"], "login", time.time()))
    connection.commit(); connection.close()
    response = JSONResponse({"user": _user_dict(row)})
    _set_session_cookie(response, token, expires_at)
    return response


@router.get("/api/auth/me")
def me(request: Request):
    return {"user": request.state.user}


@router.delete("/api/auth/logout")
def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE, "")
    connection = db()
    connection.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    connection.commit(); connection.close()
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.put("/api/auth/change-password")
def change_password(request: Request, payload: dict[str, Any]):
    current, new_password = str(payload.get("current_password", "")), _validate_new_password(payload.get("new_password"))
    connection = db()
    row = connection.execute("SELECT * FROM users WHERE id=?", (request.state.user["id"],)).fetchone()
    if not row or not verify_password(current, row["password_salt"], row["password_hash"]):
        connection.close(); raise HTTPException(401, "Current password is incorrect")
    salt, password_hash = hash_password(new_password)
    connection.execute("UPDATE users SET password_salt=?,password_hash=?,must_change_password=0,updated_at=? WHERE id=?", (salt, password_hash, time.time(), row["id"]))
    connection.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    token, expires_at = _create_session(connection, row["id"])
    connection.commit(); connection.close()
    response = JSONResponse({"user": {**request.state.user, "must_change_password": False}})
    _set_session_cookie(response, token, expires_at)
    return response


def record_image_open(user_id: str, image_id: str) -> None:
    connection = db()
    connection.execute("INSERT INTO image_activity(user_id,image_id,opened_at) VALUES(?,?,?) ON CONFLICT(user_id,image_id) DO UPDATE SET opened_at=excluded.opened_at", (user_id, image_id, time.time()))
    connection.commit(); connection.close()


def record_event(user_id: str, event_type: str, project_id: str | None = None, image_id: str | None = None, value: int = 0, track_elapsed: bool = False) -> None:
    connection = db(); elapsed = 0.0
    if track_elapsed and image_id:
        opened = connection.execute("SELECT opened_at FROM image_activity WHERE user_id=? AND image_id=?", (user_id, image_id)).fetchone()
        if opened: elapsed = max(0.0, min(7200.0, time.time() - opened["opened_at"]))
        connection.execute("DELETE FROM image_activity WHERE user_id=? AND image_id=?", (user_id, image_id))
    connection.execute("INSERT INTO user_events(user_id,event_type,project_id,image_id,value,elapsed_seconds,created_at) VALUES(?,?,?,?,?,?,?)", (user_id, event_type, project_id, image_id, int(value), elapsed, time.time()))
    connection.commit(); connection.close()


def _performance_rows(connection: sqlite3.Connection, user_id: str | None = None):
    where, args = ("WHERE u.id=?", [user_id]) if user_id else ("", [])
    return connection.execute(f"""SELECT u.id,u.username,u.display_name,u.role,u.active,u.must_change_password,u.created_at,
      COALESCE(SUM(CASE WHEN e.event_type='image_save' THEN 1 ELSE 0 END),0) images_saved,
      COALESCE(SUM(CASE WHEN e.event_type='image_save' THEN e.value ELSE 0 END),0) boxes_saved,
      COALESCE(SUM(CASE WHEN e.event_type='prelabel_start' THEN 1 ELSE 0 END),0) prelabel_runs,
      COALESCE(SUM(CASE WHEN e.event_type='prelabel_start' THEN e.value ELSE 0 END),0) prelabel_images,
      COALESCE(SUM(e.elapsed_seconds),0) active_seconds,
      MAX(e.created_at) last_active
      FROM users u LEFT JOIN user_events e ON e.user_id=u.id {where}
      GROUP BY u.id ORDER BY images_saved DESC,u.username""", args).fetchall()


@router.get("/api/performance/me")
def performance_me(request: Request):
    connection = db(); row = _performance_rows(connection, request.state.user["id"])[0]; connection.close()
    return {"performance": _user_dict(row)}


def _require_admin(request: Request) -> None:
    if request.state.user["role"] != "admin": raise HTTPException(403, "Admin access required")


@router.get("/api/admin/users")
def admin_users(request: Request):
    _require_admin(request); connection = db(); rows = _performance_rows(connection); connection.close()
    return {"users": [_user_dict(row) for row in rows]}


@router.put("/api/admin/users/{user_id}")
def admin_update_user(user_id: str, request: Request, payload: dict[str, Any]):
    _require_admin(request)
    if user_id == request.state.user["id"] and payload.get("active") is False:
        raise HTTPException(400, "Admin cannot disable the current account")
    if user_id == request.state.user["id"] and payload.get("role") == "annotator":
        raise HTTPException(400, "Admin cannot demote the current account")
    connection = db()
    target = connection.execute("SELECT role,active FROM users WHERE id=?", (user_id,)).fetchone()
    active_admins = connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
    if not target:
        connection.close()
        raise HTTPException(404, "User not found")
    removes_active_admin = target["role"] == "admin" and target["active"] and (payload.get("active") is False or payload.get("role") == "annotator")
    if removes_active_admin and active_admins <= 1:
        connection.close()
        raise HTTPException(400, "At least one active admin is required")
    updates, args = [], []
    if "active" in payload: updates.append("active=?"); args.append(1 if payload["active"] else 0)
    if payload.get("role") in {"admin", "annotator"}: updates.append("role=?"); args.append(payload["role"])
    if "display_name" in payload: updates.append("display_name=?"); args.append(str(payload["display_name"]).strip()[:80])
    if payload.get("new_password"):
        salt, password_hash = hash_password(_validate_new_password(payload["new_password"]))
        updates += ["password_salt=?", "password_hash=?", "must_change_password=1"]
        args += [salt, password_hash]
    if not updates:
        connection.close()
        raise HTTPException(422, "No valid changes")
    updates.append("updated_at=?"); args += [time.time(), user_id]
    connection.execute(f"UPDATE users SET {','.join(updates)} WHERE id=?", args)
    if payload.get("new_password") or payload.get("active") is False: connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    connection.commit(); connection.close()
    return {"status": "ok"}


@router.get("/api/admin/settings")
def admin_settings(request: Request):
    _require_admin(request); connection = db(); row = connection.execute("SELECT value FROM app_settings WHERE key='registration_enabled'").fetchone(); connection.close()
    return {"registration_enabled": bool(row and row["value"] == "1")}


@router.put("/api/admin/settings")
def admin_update_settings(request: Request, payload: dict[str, Any]):
    _require_admin(request); enabled = "1" if payload.get("registration_enabled") else "0"
    connection = db(); connection.execute("INSERT INTO app_settings(key,value) VALUES('registration_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (enabled,)); connection.commit(); connection.close()
    return {"registration_enabled": enabled == "1"}
