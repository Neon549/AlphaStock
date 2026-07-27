#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
api/auth.py
用户注册/登录模块  ──  PostgreSQL 版

存储：PostgreSQL（users + tokens 表，由 db.init_db() 创建）
密码：SHA256 + salt 哈希，不存明文
Token：UUID hex，登录后返回
"""

import hashlib
import uuid
from datetime import datetime

from db import execute

# ── 内部工具 ──────────────────────────────────────────────────────────


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((password + salt).encode("utf-8")).hexdigest()


# ── 公开接口 ──────────────────────────────────────────────────────────


def register(username: str, password: str, email: str = "") -> dict:
    """
    注册新用户
    返回：{success, message, token, username}
    """
    username = (username or "").strip()
    password = (password or "").strip()
    email = (email or "").strip() or None

    if len(username) < 2:
        return {"success": False, "message": "用户名至少2个字符"}
    if len(password) < 4:
        return {"success": False, "message": "密码至少4个字符"}

    row = execute(
        "SELECT username FROM users WHERE username = %s",
        (username,),
        fetch="one",
    )
    if row:
        return {"success": False, "message": "用户名已存在"}

    if email:
        row = execute("SELECT username FROM users WHERE email = %s", (email,), fetch="one")
        if row:
            return {"success": False, "message": "该邮箱已被注册"}

    salt = uuid.uuid4().hex
    password_hash = _hash_password(password, salt)
    token = uuid.uuid4().hex
    now = datetime.now().isoformat()

    execute(
        "INSERT INTO users (username, password_hash, salt, email, created_at) VALUES (%s, %s, %s, %s, %s)",
        (username, password_hash, salt, email, now),
    )
    execute(
        "INSERT INTO tokens (token, username, created_at) VALUES (%s, %s, %s)",
        (token, username, now),
    )

    return {
        "success": True,
        "message": "注册成功",
        "token": token,
        "username": username,
    }


def login(username: str, password: str) -> dict:
    """
    用户登录
    返回：{success, message, token, username}
    """
    username = (username or "").strip()
    password = (password or "").strip()

    row = execute(
        "SELECT password_hash, salt FROM users WHERE username = %s",
        (username,),
        fetch="one",
    )
    if not row:
        return {"success": False, "message": "用户名不存在"}

    password_hash, salt = row
    if _hash_password(password, salt) != password_hash:
        return {"success": False, "message": "密码错误"}

    token = uuid.uuid4().hex
    now = datetime.now().isoformat()
    execute(
        "INSERT INTO tokens (token, username, created_at) VALUES (%s, %s, %s)",
        (token, username, now),
    )

    return {
        "success": True,
        "message": "登录成功",
        "token": token,
        "username": username,
    }


def verify_token(token: str) -> dict:
    """
    验证 token 有效性
    返回：{valid, username}
    """
    if not token:
        return {"valid": False, "username": ""}

    row = execute(
        "SELECT username FROM tokens WHERE token = %s",
        (token,),
        fetch="one",
    )
    if row:
        return {"valid": True, "username": row[0]}
    return {"valid": False, "username": ""}


def logout(token: str) -> dict:
    """登出，删除 token"""
    execute("DELETE FROM tokens WHERE token = %s", (token,))
    return {"success": True, "message": "已登出"}
