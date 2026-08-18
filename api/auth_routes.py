"""Lightweight authentication and user-owned conversation routes."""

from __future__ import annotations

import datetime
import json as _json
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from api.auth import login as _login
from api.auth import logout as _logout
from api.auth import register as _register
from api.auth import verify_token as _verify
from api.auth_reset import router as reset_router
from api.auth_social import router as social_router
from api.security import require_actor, require_owned_conversation
from db import execute


router = APIRouter()
router.include_router(reset_router)
router.include_router(social_router)


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


def _authenticated(
    x_auth_token: str | None,
    authorization: str | None,
) -> str:
    return require_actor(x_auth_token=x_auth_token, authorization=authorization)


@router.get("/conversations/{username}")
def get_conversations(
    username: str,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = _authenticated(x_auth_token, authorization)
    requested = unquote(username)
    if requested != actor_id:
        raise HTTPException(status_code=403, detail="cannot access another user's conversations")
    rows = execute(
        """
        SELECT id, title, messages
        FROM conversations_store
        WHERE username = %s
        ORDER BY updated_at DESC
        LIMIT 20
        """,
        (actor_id,),
        fetch="all",
    )
    return {
        "conversations": [
            {"id": row[0], "title": row[1], "messages": _json.loads(row[2])}
            for row in (rows or [])
        ]
    }


class ConvSaveRequest(BaseModel):
    id: str
    username: str
    title: str
    messages: list


@router.post("/conversations/save")
def save_conversation(
    request: ConvSaveRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = _authenticated(x_auth_token, authorization)
    if unquote(request.username) != actor_id:
        raise HTTPException(status_code=403, detail="cannot save for another user")
    if len(request.id) > 128 or len(request.title) > 200 or len(request.messages) > 500:
        raise HTTPException(status_code=400, detail="conversation payload is too large")

    existing = execute("SELECT username FROM conversations_store WHERE id = %s", (request.id,), fetch="one")
    if existing and existing[0] != actor_id:
        raise HTTPException(status_code=403, detail="conversation belongs to another user")
    execute(
        """
        INSERT INTO conversations_store (id, username, title, messages, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            messages = EXCLUDED.messages,
            updated_at = EXCLUDED.updated_at
        """,
        (
            request.id,
            actor_id,
            request.title,
            _json.dumps(request.messages, ensure_ascii=False),
            datetime.datetime.now(datetime.timezone.utc),
        ),
    )
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: str,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = _authenticated(x_auth_token, authorization)
    require_owned_conversation(conv_id, actor_id)
    execute("DELETE FROM conversations_store WHERE id = %s AND username = %s", (conv_id, actor_id))
    return {"ok": True}
