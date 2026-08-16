"""Durable session snapshots for the agent runtime.

This deliberately reuses the existing ``checkpoints`` table with a namespaced
thread id.  It is additive at the data level: no production table migration,
old checkpoint rewrite, or evaluation-data write is required.
"""

from __future__ import annotations

import os
import json
import re
import threading
from pathlib import Path
from typing import Protocol

from agent_runtime.harness.state import RunState
from config.runtime_paths import RUNTIME_DIR


class SessionStore(Protocol):
    def save(self, state: RunState) -> None: ...
    def load(self, run_id: str) -> RunState | None: ...


class MemoryStore:
    """Thread-safe local fallback used when PostgreSQL is not configured."""

    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def save(self, state: RunState) -> None:
        with self._lock:
            self._items[state.run_id] = state.to_dict()

    def load(self, run_id: str) -> RunState | None:
        with self._lock:
            value = self._items.get(run_id)
        return RunState.from_dict(value) if value else None


class FileStore:
    """Crash-resilient local fallback; each session is an atomic JSON snapshot."""

    _SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or RUNTIME_DIR / "harness"
        self._lock = threading.Lock()

    def _path(self, run_id: str) -> Path:
        if not self._SAFE_ID.fullmatch(run_id):
            raise ValueError("invalid harness run id")
        return self.root / f"{run_id}.json"

    def save(self, state: RunState) -> None:
        path = self._path(state.run_id)
        payload = json.dumps(state.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, path)

    def load(self, run_id: str) -> RunState | None:
        path = self._path(run_id)
        with self._lock:
            try:
                payload = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
        return RunState.from_dict(json.loads(payload))


class PostgresStore:
    """Optional persistent store backed by the project's existing table."""

    prefix = "harness:"

    @staticmethod
    def _enabled() -> bool:
        # Do not import db.py only to discover configuration: that creates a
        # ten-second connection attempt per checkpoint in CI/offline mode.
        # API startup already loads its dotenv configuration; deployments that
        # want durable sessions can also set this environment variable in the
        # service definition.
        return bool(os.getenv("POSTGRES_DSN", "").strip())

    def save(self, state: RunState) -> None:
        if not self._enabled():
            # Local FileStore is the normal development/CI backend.  Lack of a
            # configured PostgreSQL service is not an outage and should not
            # create a misleading degradation event.
            return
        from db import get_conn
        from psycopg2.extras import Json

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO checkpoints (thread_id, checkpoint, metadata)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (thread_id) DO UPDATE SET
                        checkpoint = EXCLUDED.checkpoint,
                        metadata = EXCLUDED.metadata,
                        created_at = NOW()
                    """,
                    (
                        self.prefix + state.run_id,
                        Json(state.to_dict()),
                        Json({"kind": "alphastock_harness", "profile": state.profile}),
                    ),
                )
            conn.commit()

    def load(self, run_id: str) -> RunState | None:
        if not self._enabled():
            return None
        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT checkpoint FROM checkpoints WHERE thread_id = %s",
                    (self.prefix + run_id,),
                )
                row = cur.fetchone()
        return RunState.from_dict(row[0]) if row else None


class SafeStore:
    """Persist when available, while keeping the running task usable on outage."""

    def __init__(self, primary: SessionStore | None = None, fallback: SessionStore | None = None) -> None:
        self.primary = primary or PostgresStore()
        self.fallback = fallback or FileStore()
        self._primary_healthy = True
        self._lock = threading.Lock()

    def save(self, state: RunState) -> None:
        self.fallback.save(state)
        with self._lock:
            primary_healthy = self._primary_healthy
        if not primary_healthy:
            return
        try:
            self.primary.save(state)
        except Exception:
            # Audit/store degradation must not turn a read-only research task
            # into an unsafe or unavailable task.  The local atomic snapshot
            # survives a restart, while this breaker avoids repeated DB stalls.
            with self._lock:
                self._primary_healthy = False
            state.record("session_store_degraded", fallback="local_snapshot")
            self.fallback.save(state)

    def load(self, run_id: str) -> RunState | None:
        with self._lock:
            primary_healthy = self._primary_healthy
        if primary_healthy:
            try:
                state = self.primary.load(run_id)
                if state is not None:
                    self.fallback.save(state)
                    return state
            except Exception:
                with self._lock:
                    self._primary_healthy = False
        return self.fallback.load(run_id)
