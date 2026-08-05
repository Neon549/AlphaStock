"""HTTP authentication and tenant/session ownership helpers."""

from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException

from api.auth import verify_token
from db import execute


_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def extract_auth_token(
    *,
    body_token: str | None = None,
    x_auth_token: str | None = None,
    authorization: str | None = None,
) -> str:
    """Accept the legacy body token while preferring a standard header."""

    if x_auth_token and x_auth_token.strip():
        return x_auth_token.strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return (body_token or "").strip()


def require_actor(
    *,
    body_token: str | None = None,
    x_auth_token: str | None = None,
    authorization: str | None = None,
) -> str:
    token = extract_auth_token(
        body_token=body_token, x_auth_token=x_auth_token, authorization=authorization
    )
    identity = verify_token(token)
    if not identity.get("valid"):
        raise HTTPException(status_code=401, detail="authentication required")
    return str(identity["username"])


def validate_session_id(session_id: str | None) -> str:
    value = (session_id or "").strip()
    if not _SESSION_ID.fullmatch(value) or value == "default_session":
        raise HTTPException(status_code=400, detail="a valid session_id is required")
    return value


def claim_session(session_id: str | None, actor_id: str) -> str:
    """Create or verify the owner of a temporary upload session."""

    value = validate_session_id(session_id)
    row = execute("SELECT actor_id FROM upload_sessions WHERE session_id = %s", (value,), fetch="one")
    if row and row[0] != actor_id:
        raise HTTPException(status_code=403, detail="session belongs to another user")
    if not row:
        execute(
            "INSERT INTO upload_sessions (session_id, actor_id) VALUES (%s, %s) ON CONFLICT (session_id) DO NOTHING",
            (value, actor_id),
        )
        row = execute("SELECT actor_id FROM upload_sessions WHERE session_id = %s", (value,), fetch="one")
        if not row or row[0] != actor_id:
            raise HTTPException(status_code=403, detail="session belongs to another user")
    return value


def require_owned_conversation(conv_id: str, actor_id: str) -> None:
    row = execute("SELECT username FROM conversations_store WHERE id = %s", (conv_id,), fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="conversation not found")
    if row[0] != actor_id:
        raise HTTPException(status_code=403, detail="conversation belongs to another user")
