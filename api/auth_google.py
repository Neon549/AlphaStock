"""Google OAuth routes with server-side token verification."""

from __future__ import annotations

import os
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.auth import issue_token
from db import execute


router = APIRouter()
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "https://alphastock.cloud/api/v1/auth/google/callback"
)
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://alphastock.cloud").rstrip("/")


def _upsert_google_user(email: str, name: str, google_id: str) -> str:
    row = execute(
        "SELECT username FROM users WHERE google_id = %s OR username = %s",
        (google_id, email),
        fetch="one",
    )
    if row:
        username = row[0]
        execute(
            "UPDATE users SET email = %s, google_id = %s WHERE username = %s",
            (email, google_id, username),
        )
    else:
        username = email
        execute(
            """
            INSERT INTO users (username, password_hash, salt, email, google_id, token)
            VALUES (%s, '', '', %s, %s, '')
            ON CONFLICT (username) DO UPDATE SET email = EXCLUDED.email, google_id = EXCLUDED.google_id
            """,
            (username, email, google_id),
        )
    return issue_token(username)


@router.get("/auth/google")
def google_login():
    if not CLIENT_ID:
        raise HTTPException(503, detail="Google login is not configured")
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@router.get("/auth/google/callback")
async def google_callback(code: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}?login_error={error}")
    if not code or not CLIENT_ID or not CLIENT_SECRET:
        return RedirectResponse(f"{FRONTEND_URL}?login_error=google_not_configured")

    async with httpx.AsyncClient(timeout=8.0) as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse(f"{FRONTEND_URL}?login_error=token_failed")
        user_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if user_resp.status_code != 200:
        return RedirectResponse(f"{FRONTEND_URL}?login_error=userinfo_failed")
    user_info = user_resp.json()
    email = str(user_info.get("email") or "").strip().lower()
    name = str(user_info.get("name") or email.split("@")[0]).strip()
    google_id = str(user_info.get("id") or "").strip()
    if not email or not google_id:
        return RedirectResponse(f"{FRONTEND_URL}?login_error=invalid_profile")

    token = _upsert_google_user(email, name, google_id)
    return RedirectResponse(
        f"{FRONTEND_URL}?" + urlencode({"google_login": "success", "token": token, "username": email})
    )


class GoogleTokenRequest(BaseModel):
    access_token: str = ""
    id_token: str = ""


@router.post("/auth/google/token")
async def google_token_login(request: GoogleTokenRequest):
    """Verify the access token at Google; never trust client profile claims."""

    if not request.access_token and not request.id_token:
        raise HTTPException(400, detail="Google token is required")
    async with httpx.AsyncClient(timeout=8.0) as client:
        if request.access_token:
            response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {request.access_token}"},
            )
        else:
            # Google validates the signed ID token server-side.  The claims
            # posted by the browser are never used as identity evidence.
            response = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": request.id_token},
            )
    if response.status_code != 200:
        raise HTTPException(401, detail="Google token is invalid")
    user_info = response.json()
    email = str(user_info.get("email") or "").strip().lower()
    name = str(user_info.get("name") or email.split("@")[0]).strip()
    google_id = str(user_info.get("id") or "").strip()
    if not email or not google_id:
        raise HTTPException(401, detail="Google account verification failed")
    return {
        "token": _upsert_google_user(email, name, google_id),
        "username": email,
        "display_name": name or email.split("@")[0],
    }
