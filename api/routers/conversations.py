"""Authenticated conversation persistence endpoints."""

from __future__ import annotations

import datetime
import json
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from api.security import require_actor, require_owned_conversation
from db import execute


router = APIRouter(tags=["conversations"])


class ConversationSaveRequest(BaseModel):
    id: str
    username: str
    title: str
    messages: list


@router.get("/conversations/{username}")
def get_conversations(
    username: str,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    username = unquote(username)
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    if username != actor_id:
        raise HTTPException(status_code=403, detail="cannot access another user's conversations")
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
            {"id": row[0], "title": row[1], "messages": json.loads(row[2])}
            for row in (rows or [])
        ]
    }


@router.post("/conversations/save")
def save_conversation(
    request: ConversationSaveRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    username = unquote(request.username)
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    if username != actor_id:
        raise HTTPException(status_code=403, detail="cannot save for another user")
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
            username,
            request.title,
            json.dumps(request.messages, ensure_ascii=False),
            datetime.datetime.now().isoformat(),
        ),
    )
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
def delete_conversation(
    conv_id: str,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    require_owned_conversation(conv_id, actor_id)
    execute("DELETE FROM conversations_store WHERE id = %s AND username = %s", (conv_id, actor_id))
    return {"ok": True}
