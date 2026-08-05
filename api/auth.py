"""User authentication backed by PostgreSQL.

New credentials use scrypt and new access tokens are opaque, expiring values
whose SHA-256 digest is stored in the database.  Legacy SHA-256 passwords and
raw UUID tokens are accepted only long enough to migrate them on use.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from db import execute


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{2,64}$")
_TOKEN_TTL = timedelta(hours=max(1, int(os.getenv("AUTH_TOKEN_TTL_HOURS", "24"))))
_LOGIN_WINDOW_SECONDS = 300
_MAX_LOGIN_ATTEMPTS = 8
_login_attempts: dict[str, list[float]] = {}


def _hash_password(password: str, salt: str) -> str:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    )
    return "scrypt$" + derived.hex()


def _legacy_hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(username: str) -> str:
    """Issue an opaque token and store only its digest."""

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    execute(
        "INSERT INTO tokens (token, username, created_at, expires_at, revoked_at) VALUES (%s, %s, %s, %s, NULL)",
        (_token_digest(token), username, now, now + _TOKEN_TTL),
    )
    return token


def _check_login_rate(username: str) -> bool:
    now = time.monotonic()
    key = username.lower()
    attempts = [stamp for stamp in _login_attempts.get(key, []) if now - stamp < _LOGIN_WINDOW_SECONDS]
    if len(attempts) >= _MAX_LOGIN_ATTEMPTS:
        _login_attempts[key] = attempts
        return False
    attempts.append(now)
    _login_attempts[key] = attempts
    return True


def _clear_login_rate(username: str) -> None:
    _login_attempts.pop(username.lower(), None)


def register(username: str, password: str, email: str = "") -> dict:
    username = (username or "").strip()
    password = password or ""
    email = (email or "").strip().lower() or None

    if not _USERNAME_RE.fullmatch(username):
        return {"success": False, "message": "用户名格式不合法"}
    if len(password) < 8:
        return {"success": False, "message": "密码至少8个字符"}

    row = execute("SELECT username FROM users WHERE username = %s", (username,), fetch="one")
    if row:
        return {"success": False, "message": "用户名或邮箱已存在"}
    if email:
        row = execute("SELECT username FROM users WHERE email = %s", (email,), fetch="one")
        if row:
            return {"success": False, "message": "用户名或邮箱已存在"}

    salt = secrets.token_bytes(16).hex()
    now = datetime.now(timezone.utc)
    execute(
        "INSERT INTO users (username, password_hash, salt, email, created_at) VALUES (%s, %s, %s, %s, %s)",
        (username, _hash_password(password, salt), salt, email, now),
    )
    token = issue_token(username)
    return {"success": True, "message": "注册成功", "token": token, "username": username}


def login(username: str, password: str) -> dict:
    username = (username or "").strip()
    password = password or ""
    if not _USERNAME_RE.fullmatch(username) or not _check_login_rate(username):
        return {"success": False, "message": "用户名或密码错误"}

    row = execute(
        "SELECT password_hash, salt FROM users WHERE username = %s",
        (username,),
        fetch="one",
    )
    if not row:
        return {"success": False, "message": "用户名或密码错误"}

    password_hash, salt = row
    candidate = (
        _hash_password(password, salt)
        if str(password_hash).startswith("scrypt$")
        else _legacy_hash_password(password, salt)
    )
    if not hmac.compare_digest(candidate, password_hash):
        return {"success": False, "message": "用户名或密码错误"}

    # Migrate legacy passwords after a successful proof of possession.
    if not str(password_hash).startswith("scrypt$"):
        new_salt = secrets.token_bytes(16).hex()
        execute(
            "UPDATE users SET password_hash = %s, salt = %s WHERE username = %s",
            (_hash_password(password, new_salt), new_salt, username),
        )
    _clear_login_rate(username)
    token = issue_token(username)
    return {"success": True, "message": "登录成功", "token": token, "username": username}


def verify_token(token: str) -> dict:
    if not token:
        return {"valid": False, "username": ""}

    digest = _token_digest(token)
    row = execute(
        "SELECT username, created_at, expires_at, revoked_at FROM tokens WHERE token = %s",
        (digest,),
        fetch="one",
    )
    # Bounded compatibility path for old raw UUID tokens.
    if not row:
        row = execute(
            "SELECT username, created_at, expires_at, revoked_at FROM tokens WHERE token = %s",
            (token,),
            fetch="one",
        )
    if not row:
        return {"valid": False, "username": ""}

    username, created_at, expires_at, revoked_at = row
    if revoked_at:
        return {"valid": False, "username": ""}
    if expires_at is None:
        expires_at = created_at + _TOKEN_TTL
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        return {"valid": False, "username": ""}
    return {"valid": True, "username": username}


def logout(token: str) -> dict:
    if token:
        execute("DELETE FROM tokens WHERE token IN (%s, %s)", (_token_digest(token), token))
    return {"success": True, "message": "已退出"}
