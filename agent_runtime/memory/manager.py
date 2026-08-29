"""Safe memory composition for the investment runtime.

This module intentionally does *not* store raw market data, unreviewed model
output or tool text as long-term memory. Those remain timestamped evidence or
execution trace. Memory holds only user-provided preferences and a bounded
session state that helps continue a task.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Protocol

from control_plane.contracts import AgentEvent


class MemoryManager(Protocol):
    def load_context(self, event: AgentEvent, stock_code: str | None = None) -> dict[str, Any]: ...
    def remember_run(
        self,
        event: AgentEvent,
        parsed: dict[str, Any],
        workflow_result: dict[str, Any] | None,
        response_text: str = "",
        run_id: str | None = None,
    ) -> None: ...


class NullMemoryManager:
    """Dependency-free default for tests and one-shot local scripts."""

    def load_context(self, event: AgentEvent, stock_code: str | None = None) -> dict[str, Any]:
        return {
            "schema_version": "memory-context/v1",
            "session": {},
            "preferences": {},
            "recent_transcript": [],
        }

    def remember_run(
        self,
        event: AgentEvent,
        parsed: dict[str, Any],
        workflow_result: dict[str, Any] | None,
        response_text: str = "",
        run_id: str | None = None,
    ) -> None:
        return None


def _safe_session_summary(parsed: dict[str, Any], workflow_result: dict[str, Any] | None) -> dict[str, Any]:
    """Keep continuations useful without promoting an unreviewed draft to fact."""
    snapshot = (workflow_result or {}).get("context_snapshot") or {}
    citations = snapshot.get("document_citations") or []
    evidence_ids = [item.get("evidence_id") for item in citations if item.get("evidence_id")][:12]
    return {
        "schema_version": "session-memory/v1",
        "compaction": {
            "strategy": "structured_evidence_snapshot",
            "raw_transcript_retained": True,
            "draft_promoted_to_fact": False,
        },
        "last_intent": parsed.get("intent"),
        "last_stock_code": parsed.get("stock_code"),
        "last_analyst_focus": parsed.get("analyst_focus"),
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "unresolved_risks": list(snapshot.get("unresolved_risks") or [])[:8],
        "evidence_ids": evidence_ids,
        # A draft's decision is deliberately excluded until a human approval
        # writes it through LongTermMemory.
    }


def _capture_session_id(event: AgentEvent, run_id: str | None) -> str | None:
    """Return the persistence session only for explicit, controlled capture.

    Normal requests without a client session must not retain raw prompt text.
    The production intake window can opt in through an environment flag, while
    an individual authenticated request may opt in through ``learning_capture``.
    The generated session is internal and is never exported by the intake
    contract.
    """

    if event.session_id:
        return event.session_id
    metadata = event.metadata or {}
    enabled_by_event = bool(metadata.get("learning_capture"))
    enabled_by_window = os.getenv("ALPHASTOCK_E2E_INTAKE_CAPTURE", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if (enabled_by_event or enabled_by_window) and run_id:
        return f"capture-{run_id}"
    return None


class PostgresMemoryManager:
    """PostgreSQL implementation; only bounded summaries enter prompt context."""

    def _json(self, value: Any):
        from psycopg2.extras import Json
        return Json(value)

    def load_context(self, event: AgentEvent, stock_code: str | None = None) -> dict[str, Any]:
        from db import get_conn

        session: dict[str, Any] = {}
        preferences: dict[str, Any] = {}
        transcript: list[dict[str, str]] = []
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if event.session_id:
                        cur.execute(
                            "SELECT memory FROM agent_session_memory WHERE session_id = %s",
                            (event.session_id,),
                        )
                        row = cur.fetchone()
                        session = (row[0] if row else {}) or {}
                        cur.execute(
                            """
                            SELECT role, content FROM agent_session_transcript
                            WHERE session_id = %s
                            ORDER BY id DESC
                            LIMIT 8
                            """,
                            (event.session_id,),
                        )
                        rows = list(reversed(cur.fetchall()))
                        remaining = 2_400
                        for role, content in rows:
                            text = str(content or "").strip()
                            if not text or remaining <= 0:
                                continue
                            clipped = text[:remaining]
                            transcript.append({"role": role, "content": clipped})
                            remaining -= len(clipped)
                    if event.actor_id:
                        cur.execute(
                            "SELECT preferences FROM user_preferences WHERE actor_id = %s",
                            (event.actor_id,),
                        )
                        row = cur.fetchone()
                        preferences = (row[0] if row else {}) or {}
        except Exception as exc:
            print(f"[Memory] context lookup failed; continuing without persistent memory: {exc}")

        # A previous stock must not silently become evidence for another stock.
        if stock_code and session.get("last_stock_code") not in {None, stock_code}:
            session = {"last_stock_code": session.get("last_stock_code"), "task_switched": True}
        return {
            "schema_version": "memory-context/v1",
            "session": session,
            "preferences": preferences,
            "recent_transcript": transcript,
        }

    def remember_run(
        self,
        event: AgentEvent,
        parsed: dict[str, Any],
        workflow_result: dict[str, Any] | None,
        response_text: str = "",
        run_id: str | None = None,
    ) -> None:
        session_id = _capture_session_id(event, run_id)
        if not session_id:
            return
        memory = _safe_session_summary(parsed, workflow_result)
        try:
            from db import get_conn

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_session_memory (session_id, actor_id, memory)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (session_id) DO UPDATE SET
                            actor_id = EXCLUDED.actor_id,
                            memory = EXCLUDED.memory,
                            updated_at = NOW()
                        """,
                        (session_id, event.actor_id, self._json(memory)),
                    )
                    # Transcript is an audit/source record. Only a bounded,
                    # recent slice is later reintroduced into Context.
                    cur.execute(
                        """
                        INSERT INTO agent_session_transcript (session_id, actor_id, run_id, role, content)
                        VALUES (%s, %s, %s, 'user', %s)
                        """,
                        (session_id, event.actor_id, run_id, event.content[:12_000]),
                    )
                    if response_text:
                        cur.execute(
                            """
                            INSERT INTO agent_session_transcript (session_id, actor_id, run_id, role, content)
                            VALUES (%s, %s, %s, 'assistant', %s)
                            """,
                            (session_id, event.actor_id, run_id, response_text[:12_000]),
                        )
            # This only enqueues a durable, bounded background job. It never
            # invokes a model or writes long-term memory on the user path.
            from agent_runtime.memory.maintenance import enqueue_extraction_if_due
            enqueue_extraction_if_due(session_id, event.actor_id)
        except Exception as exc:
            print(f"[Memory] session summary write failed: {exc}")

    def get_preferences(self, actor_id: str) -> dict[str, Any]:
        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT preferences FROM user_preferences WHERE actor_id = %s", (actor_id,))
                row = cur.fetchone()
        return (row[0] if row else {}) or {}

    def set_preferences(self, actor_id: str, preferences: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            key: value for key, value in preferences.items()
            if key in {"risk_profile", "preferred_sectors", "answer_style", "watchlist"}
        }
        # Persist only user supplied, bounded preferences; nothing inferred from
        # a single request becomes a user profile automatically.
        if isinstance(allowed.get("preferred_sectors"), list):
            allowed["preferred_sectors"] = [str(item)[:40] for item in allowed["preferred_sectors"][:20]]
        if isinstance(allowed.get("watchlist"), list):
            allowed["watchlist"] = [str(item)[:6] for item in allowed["watchlist"][:30]]
        for key in ("risk_profile", "answer_style"):
            if key in allowed:
                allowed[key] = str(allowed[key])[:40]

        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_preferences (actor_id, preferences)
                    VALUES (%s, %s)
                    ON CONFLICT (actor_id) DO UPDATE SET
                        preferences = EXCLUDED.preferences, updated_at = NOW()
                    """,
                    (actor_id, self._json(allowed)),
                )
        return allowed
