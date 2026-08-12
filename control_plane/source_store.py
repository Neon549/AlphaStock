"""Durable source registry and source-change idempotency store."""

from __future__ import annotations

from typing import Protocol

from control_plane.source_registry import SourceDefinition, SourceObservation


class SourceChangeStore(Protocol):
    def ensure_source(self, source: SourceDefinition) -> None: ...
    def accept_change(self, source: SourceDefinition, observation: SourceObservation) -> bool: ...


class NullSourceChangeStore:
    """Local/test fallback; the in-process SourceRegistry remains authoritative."""

    def ensure_source(self, source: SourceDefinition) -> None:
        return None

    def accept_change(self, source: SourceDefinition, observation: SourceObservation) -> bool:
        return True


class PostgresSourceChangeStore:
    """Persist source definitions and dedupe source revisions across restarts."""

    @staticmethod
    def _json(value):
        from psycopg2.extras import Json

        return Json(value)

    def ensure_source(self, source: SourceDefinition) -> None:
        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_sources
                        (source_id, source_type, entity_key, endpoint, enabled, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_id) DO UPDATE SET
                        source_type = EXCLUDED.source_type,
                        entity_key = EXCLUDED.entity_key,
                        endpoint = EXCLUDED.endpoint,
                        enabled = EXCLUDED.enabled,
                        metadata = EXCLUDED.metadata
                    """,
                    (
                        source.source_id,
                        source.source_type,
                        source.entity_key,
                        source.endpoint,
                        source.enabled,
                        self._json(source.metadata),
                    ),
                )

    def accept_change(self, source: SourceDefinition, observation: SourceObservation) -> bool:
        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO agent_source_changes
                        (event_id, source_id, dedupe_key, source_version, content_hash, payload, observed_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedupe_key) DO NOTHING
                    RETURNING event_id
                    """,
                    (
                        observation.event_id,
                        source.source_id,
                        observation.dedupe_key,
                        observation.version,
                        observation.content_hash,
                        self._json(observation.to_metadata()),
                        observation.observed_at,
                    ),
                )
                accepted = cur.fetchone() is not None
                if accepted:
                    cur.execute(
                        """
                        UPDATE agent_sources
                           SET last_version = %s,
                               last_content_hash = %s,
                               last_observed_at = %s
                         WHERE source_id = %s
                        """,
                        (
                            observation.version,
                            observation.content_hash,
                            observation.observed_at,
                            source.source_id,
                        ),
                    )
                return accepted


__all__ = ["SourceChangeStore", "NullSourceChangeStore", "PostgresSourceChangeStore"]
