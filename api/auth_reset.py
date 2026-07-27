#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api/auth_reset.py  ──  忘记密码 / 重置密码
流程：
  1. POST /auth/forgot-password   { email } → 发送重置邮件
  2. POST /auth/reset-password    { token, new_password } → 更新密码
"""

import hashlib
import os
import smtplib
import uuid
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import execute

router = APIRouter()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://alphastock.cloud")
RESET_EXPIRE_HOURS = 1


def _send_reset_email(to_email: str, reset_token: str):
    reset_url = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "AlphaStock 密码重置"
    msg["From"] = f"AlphaStock <{SMTP_USER}>"
    msg["To"] = to_email

    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e">AlphaStock 密码重置</h2>
      <p>您申请了密码重置，点击下方按钮设置新密码：</p>
      <a href="{reset_url}"
         style="display:inline-block;padding:12px 28px;background:#4f46e5;color:#fff;
                border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0">
        重置密码
      </a>
      <p style="color:#666;font-size:13px">链接 {RESET_EXPIRE_HOURS} 小时内有效，请勿转发。<br>
      如果您未申请重置，请忽略此邮件。</p>
    </div>
    """
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

    row = execute(
        "SELECT username FROM users WHERE lower(email) = %s",
        (email,),
        fetch="one",
    )
    # 无论是否找到用户都返回相同提示，防止枚举攻击
    if not row:
        return {"ok": True, "message": "如果该邮箱已注册，您将收到重置邮件"}

    username = row[0]
    reset_token = uuid.uuid4().hex
    expires_at = (datetime.utcnow() + timedelta(hours=RESET_EXPIRE_HOURS)).isoformat()

    # 删旧的未使用 token
    execute(
        "DELETE FROM password_reset_tokens WHERE username = %s AND used = FALSE",
        (username,),
    )
    execute(
        "INSERT INTO password_reset_tokens (token, username, expires_at) VALUES (%s, %s, %s)",
        (reset_token, username, expires_at),
    )

    try:
        _send_reset_email(email, reset_token)
    except Exception as e:
        print(f"[ResetPwd] 邮件发送失败: {e}")
        raise HTTPException(500, detail="邮件发送失败，请检查 SMTP 配置")

    return {"ok": True, "message": "重置邮件已发送，请查收"}


@router.post("/auth/reset-password")
def reset_password(request: ResetRequest):
    token = (request.token or "").strip()
    new_password = (request.new_password or "").strip()

    if len(new_password) < 6:
        raise HTTPException(400, detail="新密码至少6位")

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
    if datetime.utcnow() > expires_at.replace(tzinfo=None):
        raise HTTPException(400, detail="重置链接已过期，请重新申请")

    import uuid as _uuid
    salt = _uuid.uuid4().hex
    pw_hash = hashlib.sha256((new_password + salt).encode()).hexdigest()

    execute(
        "UPDATE users SET password_hash = %s, salt = %s WHERE username = %s",
        (pw_hash, salt, username),
    )
    execute(
        "UPDATE password_reset_tokens SET used = TRUE WHERE token = %s",
        (token,),
    )
    # 使该用户所有登录 token 失效
    execute("DELETE FROM tokens WHERE username = %s", (username,))

    return {"ok": True, "message": "密码已重置，请重新登录"}
