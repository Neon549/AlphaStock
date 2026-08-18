"""微信和 QQ 网站 OAuth 登录。

密钥只从后端环境变量读取。第三方账号通过 oauth_accounts 绑定到现有
users 记录，登录成功后继续复用 AlphaStock 的 opaque token 会话体系。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from api.auth import issue_token
from db import execute


router = APIRouter()

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://alphastock.cloud").rstrip("/")
WECHAT_APP_ID = os.getenv("WECHAT_APP_ID", "")
WECHAT_APP_SECRET = os.getenv("WECHAT_APP_SECRET", "")
WECHAT_REDIRECT_URI = os.getenv(
    "WECHAT_REDIRECT_URI", "https://alphastock.cloud/api/v1/auth/wechat/callback"
)
QQ_APP_ID = os.getenv("QQ_APP_ID", "")
QQ_APP_KEY = os.getenv("QQ_APP_KEY", "")
QQ_REDIRECT_URI = os.getenv(
    "QQ_REDIRECT_URI", "https://alphastock.cloud/api/v1/auth/qq/callback"
)
OAUTH_COOKIE_MAX_AGE_SECONDS = 600
OAUTH_COOKIE_SECURE = os.getenv(
    "OAUTH_COOKIE_SECURE", "true" if FRONTEND_URL.startswith("https://") else "false"
).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SocialIdentity:
    provider: str
    provider_user_id: str
    email: str = ""
    email_verified: bool = False
    display_name: str = ""
    profile: dict | None = None


def _cookie_name(provider: str, suffix: str) -> str:
    return f"alphastock_{provider}_oauth_{suffix}"


def _callback_path(provider: str) -> str:
    return f"/api/v1/auth/{provider}/callback"


def _cookie_options(provider: str) -> dict:
    return {
        "max_age": OAUTH_COOKIE_MAX_AGE_SECONDS,
        "httponly": True,
        "secure": OAUTH_COOKIE_SECURE,
        "samesite": "lax",
        "path": _callback_path(provider),
    }


def _set_oauth_cookies(response: RedirectResponse, provider: str, state: str) -> None:
    response.set_cookie(_cookie_name(provider, "state"), state, **_cookie_options(provider))


def _clear_oauth_cookies(response: RedirectResponse, provider: str) -> RedirectResponse:
    response.delete_cookie(_cookie_name(provider, "state"), path=_callback_path(provider))
    return response


def _frontend_error(provider: str, error: str) -> RedirectResponse:
    response = RedirectResponse(
        f"{FRONTEND_URL}?" + urlencode({"login_error": f"{provider}_{error}"})
    )
    return _clear_oauth_cookies(response, provider)


def _check_state(request: Request, provider: str, state: str | None) -> bool:
    expected = request.cookies.get(_cookie_name(provider, "state"), "")
    return bool(state and expected and secrets.compare_digest(state, expected))


def _social_username(provider: str, provider_user_id: str) -> str:
    digest = hashlib.sha256(f"{provider}:{provider_user_id}".encode("utf-8")).hexdigest()
    return f"{provider}_{digest[:24]}"


def _upsert_social_user(identity: SocialIdentity) -> tuple[str, str]:
    """Find or create a local user and bind the provider identity."""

    row = execute(
        """
        SELECT u.username
        FROM oauth_accounts a
        JOIN users u ON u.id = a.user_id
        WHERE a.provider = %s AND a.provider_user_id = %s
        """,
        (identity.provider, identity.provider_user_id),
        fetch="one",
    )
    username = str(row[0]) if row else ""

    # Only a provider-verified email may link to an existing password user.
    if not username and identity.email and identity.email_verified:
        row = execute(
            "SELECT username FROM users WHERE lower(email) = lower(%s) ORDER BY id LIMIT 1",
            (identity.email,),
            fetch="one",
        )
        username = str(row[0]) if row else ""

    if not username:
        username = _social_username(identity.provider, identity.provider_user_id)
        execute(
            """
            INSERT INTO users (username, password_hash, salt, email)
            VALUES (%s, '', '', %s)
            ON CONFLICT (username) DO NOTHING
            """,
            (username, identity.email or None),
        )

    user_row = execute("SELECT id, username FROM users WHERE username = %s", (username,), fetch="one")
    if not user_row:
        raise RuntimeError("social user could not be created")

    execute(
        """
        INSERT INTO oauth_accounts
            (user_id, provider, provider_user_id, email, profile, updated_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (provider, provider_user_id) DO UPDATE SET
            email = EXCLUDED.email,
            profile = EXCLUDED.profile,
            updated_at = NOW()
        """,
        (
            user_row[0], identity.provider, identity.provider_user_id,
            identity.email or None, json.dumps(identity.profile or {}, ensure_ascii=False),
        ),
    )
    username = str(user_row[1])
    return username, issue_token(username)


def _finish_login(provider: str, identity: SocialIdentity) -> RedirectResponse:
    if not identity.provider_user_id:
        return _frontend_error(provider, "invalid_profile")
    try:
        username, token = _upsert_social_user(identity)
    except Exception:
        return _frontend_error(provider, "account_binding_failed")
    response = RedirectResponse(
        f"{FRONTEND_URL}?"
        + urlencode({
            "social_login": "success", "provider": provider, "token": token,
            "username": username, "display_name": identity.display_name or username,
        })
    )
    return _clear_oauth_cookies(response, provider)


def _require_config(provider: str) -> None:
    configured = (
        (WECHAT_APP_ID and WECHAT_APP_SECRET)
        if provider == "wechat" else (QQ_APP_ID and QQ_APP_KEY)
    )
    if not configured:
        raise HTTPException(status_code=503, detail=f"{provider} OAuth is not configured")


@router.get("/auth/wechat")
def wechat_login():
    _require_config("wechat")
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        "https://open.weixin.qq.com/connect/qrconnect?"
        + urlencode({
            "appid": WECHAT_APP_ID, "redirect_uri": WECHAT_REDIRECT_URI,
            "response_type": "code", "scope": "snsapi_login", "state": state,
        }) + "#wechat_redirect"
    )
    _set_oauth_cookies(response, "wechat", state)
    return response


@router.get("/auth/wechat/callback")
async def wechat_callback(
    request: Request, code: str | None = None, state: str | None = None,
    error: str | None = None,
):
    if not _check_state(request, "wechat", state):
        return _frontend_error("wechat", "invalid_oauth_state")
    if error:
        return _frontend_error("wechat", error)
    if not code:
        return _frontend_error("wechat", "missing_code")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            token_response = await client.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={"appid": WECHAT_APP_ID, "secret": WECHAT_APP_SECRET,
                        "code": code, "grant_type": "authorization_code"},
            )
            token_data = token_response.json()
            if token_data.get("errcode") or not token_data.get("access_token"):
                return _frontend_error("wechat", "token_failed")
            access_token = str(token_data["access_token"])
            openid = str(token_data.get("openid") or "")
            user_response = await client.get(
                "https://api.weixin.qq.com/sns/userinfo",
                params={"access_token": access_token, "openid": openid, "lang": "zh_CN"},
            )
            user_data = user_response.json()
            if user_data.get("errcode"):
                return _frontend_error("wechat", "userinfo_failed")
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return _frontend_error("wechat", "provider_unavailable")

    return _finish_login("wechat", SocialIdentity(
        provider="wechat",
        provider_user_id=str(user_data.get("unionid") or user_data.get("openid") or openid),
        display_name=str(user_data.get("nickname") or "微信用户"),
        profile=user_data,
    ))


def _qq_access_token(text: str) -> str:
    return parse_qs(text).get("access_token", [""])[0]


def _qq_openid(text: str) -> tuple[str, str]:
    match = re.search(r"callback\(\s*(\{.*\})\s*\)\s*;?", text, flags=re.DOTALL)
    if not match:
        return "", ""
    data = json.loads(match.group(1))
    return str(data.get("openid") or ""), str(data.get("client_id") or "")


@router.get("/auth/qq")
def qq_login():
    _require_config("qq")
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        "https://graph.qq.com/oauth2.0/authorize?" + urlencode({
            "response_type": "code", "client_id": QQ_APP_ID,
            "redirect_uri": QQ_REDIRECT_URI, "state": state, "scope": "get_user_info",
        })
    )
    _set_oauth_cookies(response, "qq", state)
    return response


@router.get("/auth/qq/callback")
async def qq_callback(
    request: Request, code: str | None = None, state: str | None = None,
    error: str | None = None,
):
    if not _check_state(request, "qq", state):
        return _frontend_error("qq", "invalid_oauth_state")
    if error:
        return _frontend_error("qq", error)
    if not code:
        return _frontend_error("qq", "missing_code")
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            token_response = await client.get(
                "https://graph.qq.com/oauth2.0/token",
                params={"grant_type": "authorization_code", "client_id": QQ_APP_ID,
                        "client_secret": QQ_APP_KEY, "code": code,
                        "redirect_uri": QQ_REDIRECT_URI},
            )
            access_token = _qq_access_token(token_response.text)
            if not access_token:
                return _frontend_error("qq", "token_failed")
            openid_response = await client.get(
                "https://graph.qq.com/oauth2.0/me", params={"access_token": access_token}
            )
            openid, client_id = _qq_openid(openid_response.text)
            if not openid:
                return _frontend_error("qq", "openid_failed")
            user_response = await client.get(
                "https://graph.qq.com/user/get_user_info",
                params={"access_token": access_token,
                        "oauth_consumer_key": client_id or QQ_APP_ID, "openid": openid},
            )
            user_data = user_response.json()
            if str(user_data.get("ret", "0")) != "0":
                return _frontend_error("qq", "userinfo_failed")
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return _frontend_error("qq", "provider_unavailable")

    return _finish_login("qq", SocialIdentity(
        provider="qq", provider_user_id=openid,
        display_name=str(user_data.get("nickname") or "QQ用户"), profile=user_data,
    ))
