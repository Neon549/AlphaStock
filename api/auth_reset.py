"""Single-use password reset routes."""

from __future__ import annotations

import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.auth import _hash_password, _token_digest
from db import execute


router = APIRouter()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://alphastock.cloud").rstrip("/")
RESET_EXPIRE_HOURS = 1


def _send_reset_email(to_email: str, reset_token: str) -> None:
    reset_url = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "AlphaStock password reset"
    msg["From"] = f"AlphaStock <{SMTP_USER}>"
    msg["To"] = to_email
    html = (
        "<p>You requested a password reset.</p>"
        f'<p><a href="{reset_url}">Set a new password</a></p>'
        f"<p>This link expires in {RESET_EXPIRE_HOURS} hour(s).</p>"
    )
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, to_email, msg.as_string())


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str


@router.post("/auth/forgot-password")
def forgot_password(request: ForgotRequest):
    email = (request.email or "").strip().lower()
    if not email:
        raise HTTPException(400, detail="请输入邮箱")
    token_storage_key = digest
    row = execute(
        "SELECT username FROM users WHERE lower(email) = %s",
        (email,),
        fetch="one",
    )
    # Always use the same successful response for unknown emails.
    if not row:
        return {"ok": True, "message": "如果邮箱已注册，您将收到重置邮件"}

    username = row[0]
    reset_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=RESET_EXPIRE_HOURS)
    execute(
        "DELETE FROM password_reset_tokens WHERE username = %s AND used = FALSE",
        (username,),
    )
    execute(
        "INSERT INTO password_reset_tokens (token, username, expires_at) VALUES (%s, %s, %s)",
        (_token_digest(reset_token), username, expires_at),
    )
    try:
        _send_reset_email(email, reset_token)
    except Exception:
        # Do not expose SMTP details to an unauthenticated caller.
        raise HTTPException(503, detail="重置邮件暂时无法发送")
    return {"ok": True, "message": "重置邮件已发送，请查收"}


@router.post("/auth/reset-password")
def reset_password(request: ResetRequest):
    token = (request.token or "").strip()
    new_password = request.new_password or ""
    if len(new_password) < 8:
        raise HTTPException(400, detail="新密码至少8个字符")

    digest = _token_digest(token)
    row = execute(
        "SELECT username, expires_at, used FROM password_reset_tokens WHERE token = %s",
        (digest,),
        fetch="one",
    )
    if not row:
        # Compatibility for reset links issued before token hashing was added.
        token_storage_key = token
        row = execute(
            "SELECT username, expires_at, used FROM password_reset_tokens WHERE token = %s",
            (token,),
            fetch="one",
        )
    if not row:
        raise HTTPException(400, detail="无效的重置链接")

    username, expires_at, used = row
    if used:
        raise HTTPException(400, detail="该链接已使用，请重新申请")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(400, detail="重置链接已过期，请重新申请")

    salt = secrets.token_bytes(16).hex()
    execute(
        "UPDATE users SET password_hash = %s, salt = %s WHERE username = %s",
        (_hash_password(new_password, salt), salt, username),
    )
    execute("UPDATE password_reset_tokens SET used = TRUE WHERE token = %s", (token_storage_key,))
    execute("DELETE FROM tokens WHERE username = %s", (username,))
    return {"ok": True, "message": "密码已重置，请重新登录"}
