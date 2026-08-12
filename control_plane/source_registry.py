"""Source registration and revision detection for event-driven ingestion.

This module deliberately has no HTTP, database or LLM dependency.  A Cron
watcher, provider webhook adapter, or database CDC consumer can all feed the
same ``SourceRegistry.observe`` method and receive the same stable event
identity.  Persistence is added by the control-plane store, not by the
detector itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any


def _digest(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def revision_dedupe_key(source_id: str, version: str | None, content_hash: str | None) -> str:
    """Return the cross-transport identity of one source revision."""

    return hashlib.sha256(
        f"{source_id}:{version or ''}:{content_hash or ''}".encode("utf-8")
    ).hexdigest()


def revision_event_id(dedupe_key: str) -> str:
    return hashlib.sha256(f"source.changed:{dedupe_key}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceDefinition:
    """A source that can produce news, filings, market or strategy updates."""

    source_id: str
    source_type: str
    entity_key: str | None = None
    endpoint: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.source_type.strip():
            raise ValueError("source_type is required")

    @property
    def affected_symbols(self) -> tuple[str, ...]:
        values = self.metadata.get("affected_symbols") or self.metadata.get("symbols") or ()
        if isinstance(values, str):
            values = (values,)
        return tuple(str(value) for value in values if str(value).strip())


@dataclass(frozen=True)
class SourceObservation:
    """The result of checking one registered source."""

    source_id: str
    source_type: str
    version: str | None
    content_hash: str | None
    observed_at: str
    changed: bool
    dedupe_key: str
    affected_symbols: tuple[str, ...] = ()
    endpoint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def event_id(self) -> str:
        return revision_event_id(self.dedupe_key)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_version": self.version,
            "content_hash": self.content_hash,
            "observed_at": self.observed_at,
            "affected_symbols": list(self.affected_symbols),
            "source_endpoint": self.endpoint,
            "source_metadata": dict(self.metadata),
            "dedupe_key": self.dedupe_key,
        }


class SourceRegistry:
    """Register sources and detect revisions within one watcher process.

    The first observation is treated as a change so a newly registered source
    can be indexed.  Re-observing the same version/hash returns ``changed``
    false and must not emit an event.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, SourceDefinition] = {}
        self._last_dedupe_keys: dict[str, str] = {}

    def register(self, source: SourceDefinition) -> SourceDefinition:
        self._definitions[source.source_id] = source
        return source

    def get(self, source_id: str) -> SourceDefinition:
        try:
            return self._definitions[source_id]
        except KeyError as exc:
            raise KeyError(f"source is not registered: {source_id}") from exc

    def list(self, *, enabled_only: bool = True) -> list[SourceDefinition]:
        values = self._definitions.values()
        return [item for item in values if not enabled_only or item.enabled]

    def observe(
        self,
        source_id: str,
        *,
        version: str | None = None,
        content: bytes | str | None = None,
        content_hash: str | None = None,
        observed_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> SourceObservation:
        source = self.get(source_id)
        if not source.enabled:
            raise ValueError(f"source is disabled: {source_id}")
        digest = content_hash or _digest(content)
        if not version and not digest:
            raise ValueError("version or content/content_hash is required")
        dedupe_key = revision_dedupe_key(source_id, version, digest)
        changed = self._last_dedupe_keys.get(source_id) != dedupe_key
        if commit:
            self._last_dedupe_keys[source_id] = dedupe_key
        stamp = (observed_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
        return SourceObservation(
            source_id=source.source_id,
            source_type=source.source_type,
            version=version,
            content_hash=digest,
            observed_at=stamp,
            changed=changed,
            dedupe_key=dedupe_key,
            affected_symbols=source.affected_symbols,
            endpoint=source.endpoint,
            metadata={**source.metadata, **(metadata or {})},
        )

    def inspect(
        self,
        source_id: str,
        *,
        version: str | None = None,
        content: bytes | str | None = None,
        content_hash: str | None = None,
        observed_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SourceObservation:
        """Build an observation without committing it as the latest revision.

        Ingestion workers use this two-phase form: parse/index first, then
        call :meth:`commit` only after the source content is durable.
        """

        return self.observe(
            source_id,
            version=version,
            content=content,
            content_hash=content_hash,
            observed_at=observed_at,
            metadata=metadata,
            commit=False,
        )

    def commit(self, observation: SourceObservation) -> None:
        """Mark an inspected revision as the latest accepted revision."""

        self._last_dedupe_keys[observation.source_id] = observation.dedupe_key


__all__ = [
    "SourceDefinition",
    "SourceObservation",
    "SourceRegistry",
    "revision_dedupe_key",
    "revision_event_id",
]
