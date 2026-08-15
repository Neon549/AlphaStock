from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth_google


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_google, "CLIENT_ID", "test-client-id")
    app = FastAPI()
    app.include_router(auth_google.router)
    return TestClient(app)


def test_google_login_redirect_has_state_and_scoped_secure_cookies(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/auth/google?next_page=backtest", follow_redirects=False)

    assert response.status_code == 307
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["client_id"] == ["test-client-id"]
    assert query["state"] and len(query["state"][0]) >= 32
    cookies = response.headers.get_list("set-cookie")
    assert any("alphastock_google_oauth_state=" in value and "HttpOnly" in value and "Secure" in value for value in cookies)
    assert any("alphastock_google_oauth_next_page=backtest" in value for value in cookies)


def test_google_callback_rejects_missing_or_mismatched_csrf_state(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/auth/google", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        rejected = client.get("/auth/google/callback?code=unused&state=wrong", follow_redirects=False)
        missing = client.get("/auth/google/callback?code=unused", follow_redirects=False)

    assert "login_error=invalid_oauth_state" in rejected.headers["location"]
    assert "login_error=invalid_oauth_state" in missing.headers["location"]
    assert state != "wrong"


def test_google_login_normalises_unknown_destination_to_chat(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.get("/auth/google?next_page=https://other.example", follow_redirects=False)

    cookies = response.headers.get_list("set-cookie")
    assert any("alphastock_google_oauth_next_page=chat" in value for value in cookies)
