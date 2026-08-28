"""Health contracts for process, traffic admission, and dependency diagnosis.

The probes deliberately avoid outbound provider calls: a health endpoint must
not spend model quota or amplify a provider outage. PostgreSQL is checked with
a lightweight query; provider checks report configuration and locally observed
runtime state only.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from time import perf_counter
from typing import Any, Callable, Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse


Probe = Callable[[], dict[str, Any]]


def probe_postgres() -> dict[str, Any]:
    """Check PostgreSQL and pgvector without exposing connection details."""

    started = perf_counter()
    try:
        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '2000ms'")
                cursor.execute("SELECT 1")
                cursor.fetchone()
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )
                pgvector_available = bool(cursor.fetchone()[0])
            # ``db.get_conn`` commits writes at call sites rather than on
            # context exit. End this read-only transaction before the pooled
            # connection is returned, avoiding idle-in-transaction sessions.
            conn.rollback()
        return {
            "status": "ok",
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "pgvector": "ok" if pgvector_available else "unavailable",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "error_type": type(exc).__name__,
            "pgvector": "unknown",
        }


def configured_dependencies(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return credential presence only; never return credential values."""

    env = environ if environ is not None else os.environ
    primary_model = bool(env.get("DEEPSEEK_API_KEY"))
    backup_model = bool(env.get("DASHSCOPE_API_KEY"))
    langfuse = bool(env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY"))
    return {
        "primary_model": {"status": "configured" if primary_model else "missing"},
        "backup_model": {"status": "configured" if backup_model else "not_configured"},
        "langfuse": {
            "status": "configured_unverified" if langfuse else "not_configured",
            "probe": "passive",
        },
    }


@dataclass(frozen=True)
class HealthService:
    router_state: Mapping[str, Any]
    database_probe: Probe = probe_postgres
    environ: Mapping[str, str] | None = None

    def liveness(self) -> dict[str, Any]:
        return {"status": "ok"}

    def dependencies(self) -> dict[str, Any]:
        database = self.database_probe()
        configured = configured_dependencies(self.environ)
        return {
            "status": "ok" if database.get("status") == "ok" else "degraded",
            "dependencies": {
                "postgres": {
                    key: value for key, value in database.items() if key != "pgvector"
                },
                "pgvector": {"status": database.get("pgvector", "unknown")},
                "business_router": {
                    "status": str(self.router_state.get("state") or "unknown")
                },
                "news_index": {
                    "status": str(self.router_state.get("news_index_state") or "unknown")
                },
                **configured,
            },
        }

    def readiness(self) -> tuple[dict[str, Any], int]:
        database = self.database_probe()
        configured = configured_dependencies(self.environ)
        checks = {
            "postgres": {"status": database.get("status", "failed")},
            "business_router": {
                "status": str(self.router_state.get("state") or "unknown")
            },
            "primary_model": configured["primary_model"],
        }
        ready = (
            checks["postgres"]["status"] == "ok"
            and checks["business_router"]["status"] == "ready"
            and checks["primary_model"]["status"] == "configured"
        )
        return {"status": "ready" if ready else "not_ready", "checks": checks}, 200 if ready else 503


def build_health_router(service: HealthService) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    def live():
        return service.liveness()

    @router.get("/health/ready")
    def ready():
        payload, status_code = service.readiness()
        return JSONResponse(status_code=status_code, content=payload)

    @router.get("/health/dependencies")
    def dependencies():
        payload = service.dependencies()
        status_code = 200 if payload["status"] == "ok" else 503
        return JSONResponse(status_code=status_code, content=payload)

    return router
