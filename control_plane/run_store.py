"""Persistent audit store for the framework-neutral control plane."""

from __future__ import annotations

from typing import Any, Protocol

from control_plane.contracts import AgentEvent, AgentRunResult


class RunStore(Protocol):
    def try_accept_event(self, event: AgentEvent) -> bool: ...
    def record_run(self, event: AgentEvent, result: AgentRunResult) -> None: ...
    def record_failure(self, event: AgentEvent, error: Exception) -> None: ...


class NullRunStore:
    """Test/local fallback; Gateway's process-local set still deduplicates events."""

    def try_accept_event(self, event: AgentEvent) -> bool:
        return True

    def record_run(self, event: AgentEvent, result: AgentRunResult) -> None:
        return None

    def record_failure(self, event: AgentEvent, error: Exception) -> None:
        return None


def _result_meta(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist execution metadata, never full reports/tool text/LLM context."""
    keep = (
        "intent", "stock_code", "stock_name", "analyst_focus", "status",
        "publish_status", "publish_reasons", "human_review_required", "selected_skills",
    )
    return {key: payload[key] for key in keep if key in payload}


class PostgresRunStore:
    """Uses the project's PostgreSQL pool; audit write failure never blocks analysis."""

    def _json(self, value: Any):
        from psycopg2.extras import Json
        return Json(value)

    def try_accept_event(self, event: AgentEvent) -> bool:
        try:
            from db import get_conn

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_events
                            (event_id, trigger, channel, session_id, actor_id, content_len, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (event_id) DO NOTHING
                        RETURNING event_id
                        """,
                        (
                            event.event_id, event.trigger.value, event.channel, event.session_id,
                            event.actor_id, len(event.content), self._json(event.metadata),
                        ),
                    )
                    return cur.fetchone() is not None
        except Exception as exc:
            print(f"[ControlPlane] event audit unavailable; continuing without DB idempotency: {exc}")
            return True

    def record_run(self, event: AgentEvent, result: AgentRunResult) -> None:
        try:
            from db import get_conn

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_runs (run_id, event_id, route, status, session_id, result_meta)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            result.run_id, event.event_id, result.route,
                            str(result.payload.get("status", "completed")), event.session_id,
                            self._json(_result_meta(result.payload)),
                        ),
                    )
                    for index, step in enumerate(result.trace, start=1):
                        cur.execute(
                            """
                            INSERT INTO agent_steps (run_id, step_index, event_type, detail)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (result.run_id, index, str(step.get("event", "unknown")), self._json(step)),
                        )
        except Exception as exc:
            print(f"[ControlPlane] run audit write failed: {exc}")

    def record_failure(self, event: AgentEvent, error: Exception) -> None:
        print(f"[ControlPlane] runtime failed for event={event.event_id}: {error}")
