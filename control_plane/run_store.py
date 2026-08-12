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
        "publish_status", "publish_reasons", "human_review_required", "selected_skills", "run_metrics",
        "task_plan", "task_status", "pending_confirmation", "reliability_summary", "model_degradation",
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
                    accepted = cur.fetchone() is not None
                conn.commit()
                return accepted
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
                    telemetry = result.payload.get("_run_telemetry") or {}
                    for index, call in enumerate(telemetry.get("llm_calls") or [], start=1):
                        cur.execute(
                            """
                            INSERT INTO agent_run_llm_calls
                                (run_id, call_index, model, latency_ms, success, used_backup,
                                 input_tokens, output_tokens, total_tokens, cache_hit_tokens, cache_miss_tokens,
                                 provider_role, failure_type, recovery_action, retry_delay_seconds,
                                 circuit_state, degradation_mode)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (run_id, call_index) DO NOTHING
                            """,
                            (
                                result.run_id, index, str(call.get("model") or "unknown"),
                                float(call.get("latency_ms") or 0), bool(call.get("success")),
                                bool(call.get("used_backup")), call.get("input_tokens"),
                                call.get("output_tokens"), call.get("total_tokens"),
                                call.get("prompt_cache_hit_tokens"), call.get("prompt_cache_miss_tokens"),
                                call.get("provider_role"), call.get("failure_type"), call.get("recovery_action"),
                                call.get("retry_delay_seconds"), call.get("circuit_state"), call.get("degradation_mode"),
                            ),
                        )
                    for result_ref, artifact in (telemetry.get("tool_artifacts") or {}).items():
                        cur.execute(
                            """
                            INSERT INTO agent_tool_results
                                (result_ref, tool, source_kind, citations, content, content_sha256)
                            VALUES (%s, %s, %s, %s, %s, %s)
                            ON CONFLICT (result_ref) DO NOTHING
                            """,
                            (
                                result_ref, str(artifact.get("tool") or "unknown"),
                                str(artifact.get("source_kind") or "evidence"),
                                self._json(artifact.get("citations") or []), str(artifact.get("content") or ""),
                                str(artifact.get("content_sha256") or ""),
                            ),
                        )
                        cur.execute(
                            """
                            INSERT INTO agent_run_tool_results (run_id, result_ref)
                            VALUES (%s, %s) ON CONFLICT DO NOTHING
                            """,
                            (result.run_id, result_ref),
                        )
                    # Evaluation must not make core run auditing unavailable
                    # during a partial schema rollout or a curation outage.
                    cur.execute("SAVEPOINT agent_learning_artifacts")
                    try:
                        from agent_learning.store import persist_learning_artifacts

                        persist_learning_artifacts(cur, event, result)
                    except Exception as learning_exc:
                        cur.execute("ROLLBACK TO SAVEPOINT agent_learning_artifacts")
                        print(f"[ControlPlane] learning artifact write failed: {learning_exc}")
                    finally:
                        cur.execute("RELEASE SAVEPOINT agent_learning_artifacts")
                conn.commit()
        except Exception as exc:
            print(f"[ControlPlane] run audit write failed: {exc}")

    def record_failure(self, event: AgentEvent, error: Exception) -> None:
        print(f"[ControlPlane] runtime failed for event={event.event_id}: {error}")
