from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth_social


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_social, "WECHAT_APP_ID", "wechat-app")
    monkeypatch.setattr(auth_social, "WECHAT_APP_SECRET", "wechat-secret")
    monkeypatch.setattr(auth_social, "QQ_APP_ID", "qq-app")
    monkeypatch.setattr(auth_social, "QQ_APP_KEY", "qq-key")
    monkeypatch.setattr(auth_social, "OAUTH_COOKIE_SECURE", True)
    app = FastAPI()
    app.include_router(auth_social.router)
    return TestClient(app)


def test_wechat_login_redirect_contains_scope_state_and_secure_cookie(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/auth/wechat", follow_redirects=False)

    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["appid"] == ["wechat-app"]
    assert query["scope"] == ["snsapi_login"]
    assert query["state"] and len(query["state"][0]) >= 32
    assert any("alphastock_wechat_oauth_state=" in value and "HttpOnly" in value and "Secure" in value for value in response.headers.get_list("set-cookie"))


def test_qq_login_redirect_contains_scope_state_and_secure_cookie(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/auth/qq", follow_redirects=False)

    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["qq-app"]
    assert query["scope"] == ["get_user_info"]
    assert query["state"] and len(query["state"][0]) >= 32
    assert any("alphastock_qq_oauth_state=" in value for value in response.headers.get_list("set-cookie"))


def test_social_callbacks_reject_missing_or_mismatched_state(monkeypatch):
    with _client(monkeypatch) as client:
        rejected_wechat = client.get("/auth/wechat/callback?code=unused&state=wrong", follow_redirects=False)
        rejected_qq = client.get("/auth/qq/callback?code=unused&state=wrong", follow_redirects=False)

    assert "login_error=wechat_invalid_oauth_state" in rejected_wechat.headers["location"]
    assert "login_error=qq_invalid_oauth_state" in rejected_qq.headers["location"]


def test_social_username_is_stable_and_does_not_expose_provider_id():
    first = auth_social._social_username("qq", "provider-open-id")
    second = auth_social._social_username("qq", "provider-open-id")

    assert first == second
    assert first.startswith("qq_")
    assert "provider-open-id" not in first
