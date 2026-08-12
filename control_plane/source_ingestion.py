"""Two-phase source refresh orchestration.

The worker owns ordering, not document parsing.  A concrete ingestor can call
MinerU/OCR, chunk the result and upsert pgvector.  The source revision is
committed only after that callback succeeds; then the normal Gateway event
starts the focused research run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from control_plane.contracts import AgentRunResult
from control_plane.gateway import Gateway
from control_plane.source_registry import SourceDefinition, SourceObservation, SourceRegistry
from control_plane.source_store import NullSourceChangeStore, SourceChangeStore
from control_plane.triggers import source_changed_event


@dataclass(frozen=True)
class FetchedSource:
    """Provider output used by a Cron/CDC adapter."""

    version: str | None = None
    content: bytes | str | None = None
    content_hash: str | None = None
    observed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SourceFetcher(Protocol):
    def __call__(self, source: SourceDefinition) -> FetchedSource: ...


class SourceIngestor(Protocol):
    def __call__(
        self,
        source: SourceDefinition,
        observation: SourceObservation,
        fetched: FetchedSource,
    ) -> dict[str, Any] | None: ...


class SourceIngestionWorker:
    """Fetch, durably ingest, then dispatch one source revision."""

    def __init__(
        self,
        registry: SourceRegistry,
        gateway: Gateway,
        fetcher: SourceFetcher,
        ingestor: SourceIngestor | None = None,
        store: SourceChangeStore | None = None,
    ) -> None:
        self.registry = registry
        self.gateway = gateway
        self.fetcher = fetcher
        self.ingestor = ingestor
        self.store = store or NullSourceChangeStore()

    def poll_and_dispatch(self, source_id: str) -> AgentRunResult | None:
        source = self.registry.get(source_id)
        self.store.ensure_source(source)
        fetched = self.fetcher(source)
        observation = self.registry.inspect(
            source_id,
            version=fetched.version,
            content=fetched.content,
            content_hash=fetched.content_hash,
            observed_at=fetched.observed_at,
            metadata=fetched.metadata,
        )
        if not observation.changed:
            return None

        ingestion = self.ingestor(source, observation, fetched) if self.ingestor else None
        if not self.store.accept_change(source, observation):
            self.registry.commit(observation)
            return None
        self.registry.commit(observation)
        event = source_changed_event(
            observation,
            extra_metadata={"ingestion": ingestion or {}, "ingestion_status": "completed"},
        )
        return self.gateway.dispatch(event)


__all__ = ["FetchedSource", "SourceFetcher", "SourceIngestor", "SourceIngestionWorker"]
