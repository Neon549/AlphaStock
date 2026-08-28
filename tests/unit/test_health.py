from fastapi import FastAPI
from fastapi.testclient import TestClient

import sys
import types

from api.health import (
    HealthService,
    build_health_router,
    configured_dependencies,
    probe_postgres,
)


def _client(*, router="ready", database="ok", env=None, pgvector="ok"):
    state = {"state": router, "news_index_state": "ready"}
    probe = lambda: {"status": database, "latency_ms": 1.0, "pgvector": pgvector}
    app = FastAPI()
    app.include_router(build_health_router(HealthService(state, probe, env or {})))
    return TestClient(app), state


def test_liveness_does_not_depend_on_business_dependencies():
    client, _ = _client(router="failed", database="failed")

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_accepts_traffic_only_when_required_checks_pass():
    client, _ = _client(env={"DEEPSEEK_API_KEY": "configured"})

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_fails_closed_and_does_not_expose_secrets():
    secret = "must-not-be-returned"
    client, state = _client(env={"DEEPSEEK_API_KEY": secret})
    state["state"] = "initializing"

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["business_router"]["status"] == "initializing"
    assert secret not in response.text


def test_missing_primary_model_blocks_readiness_but_optional_services_do_not():
    client, _ = _client(env={})

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["primary_model"]["status"] == "missing"


def test_dependency_report_exposes_degraded_components_without_credentials():
    env = {
        "DEEPSEEK_API_KEY": "primary-secret",
        "LANGFUSE_PUBLIC_KEY": "public-secret",
        "LANGFUSE_SECRET_KEY": "private-secret",
    }
    client, _ = _client(database="ok", pgvector="unavailable", env=env)

    response = client.get("/health/dependencies")
    payload = response.json()

    assert response.status_code == 200
    assert payload["dependencies"]["pgvector"]["status"] == "unavailable"
    assert payload["dependencies"]["langfuse"]["status"] == "configured_unverified"
    assert all(secret not in response.text for secret in env.values())


def test_database_failure_makes_dependency_endpoint_unavailable():
    client, _ = _client(database="failed", env={"DEEPSEEK_API_KEY": "configured"})

    response = client.get("/health/dependencies")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_configuration_contract_only_reports_presence():
    result = configured_dependencies({
        "DEEPSEEK_API_KEY": "secret",
        "DASHSCOPE_API_KEY": "backup",
    })

    assert result == {
        "primary_model": {"status": "configured"},
        "backup_model": {"status": "configured"},
        "langfuse": {"status": "not_configured", "probe": "passive"},
    }


def test_postgres_probe_ends_read_only_transaction_before_returning_connection(monkeypatch):
    class Cursor:
        def __init__(self):
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def execute(self, query):
            self.queries.append(query)

        def fetchone(self):
            return (True,)

    class Connection:
        def __init__(self):
            self.cursor_instance = Cursor()
            self.rolled_back = False

        def cursor(self):
            return self.cursor_instance

        def rollback(self):
            self.rolled_back = True

    connection = Connection()

    class ConnectionContext:
        def __enter__(self):
            return connection

        def __exit__(self, *_):
            return None

    monkeypatch.setitem(
        sys.modules,
        "db",
        types.SimpleNamespace(get_conn=lambda: ConnectionContext()),
    )

    result = probe_postgres()

    assert result["status"] == "ok"
    assert result["pgvector"] == "ok"
    assert connection.rolled_back is True
