"""
auth_routes.py
轻量路由：登录/注册/token验证/对话记录
不依赖 LangGraph/LangChain，启动时立即加载
"""

import json as _json
import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.auth import (
    register as _register,
    login as _login,
    verify_token as _verify,
    logout as _logout,
)
from db import execute

from api.auth_reset import router as reset_router

router = APIRouter()
router.include_router(reset_router)


# ── 认证 ──────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class TokenRequest(BaseModel):
    token: str


@router.post("/auth/register")
def auth_register(request: AuthRequest):
    result = _register(request.username, request.password, request.email)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/auth/login")
def auth_login(request: AuthRequest):
    result = _login(request.username, request.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["message"])
    return result


@router.post("/auth/verify")
def auth_verify(request: TokenRequest):
    return _verify(request.token)


@router.post("/auth/logout")
def auth_logout(request: TokenRequest):
    return _logout(request.token)


# ── 对话记录 ───────────────────────────────────────────────────────────

@router.get("/conversations/{username}")
def get_conversations(username: str):
    from urllib.parse import unquote
    username = unquote(username)
    rows = execute(
        """
        SELECT id, title, messages
        FROM conversations_store
        WHERE username = %s
        ORDER BY updated_at DESC
        LIMIT 20
        """,
        (username,),
        fetch="all",
    )
    return {
        "conversations": [
            {"id": r[0], "title": r[1], "messages": _json.loads(r[2])}
            for r in (rows or [])
        ]
    }


class ConvSaveRequest(BaseModel):
    id: str
    username: str
    title: str
    messages: list


@router.post("/conversations/save")
def save_conversation(request: ConvSaveRequest):
    from urllib.parse import unquote
    username = unquote(request.username)
    execute(
        """
        INSERT INTO conversations_store (id, username, title, messages, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            title      = EXCLUDED.title,
            messages   = EXCLUDED.messages,
            updated_at = EXCLUDED.updated_at
        """,
        (
            request.id,
            username,
            request.title,
            _json.dumps(request.messages, ensure_ascii=False),
            datetime.datetime.now().isoformat(),
        ),
    )
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    execute("DELETE FROM conversations_store WHERE id = %s", (conv_id,))
    return {"ok": True}
