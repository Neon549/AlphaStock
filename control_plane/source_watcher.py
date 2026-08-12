"""Adapters that turn source checks into Gateway events.

The watcher is intentionally transport-neutral: a scheduler can call
``observe_and_dispatch`` after polling an API, while a Webhook handler can use
the same method after verifying the provider signature.  Unchanged revisions
return ``None`` and never invoke the Agent runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from control_plane.contracts import AgentRunResult
from control_plane.gateway import Gateway
from control_plane.source_store import NullSourceChangeStore, SourceChangeStore
from control_plane.source_registry import SourceRegistry
from control_plane.triggers import source_changed_event


class SourceWatcher:
    def __init__(
        self,
        registry: SourceRegistry,
        gateway: Gateway,
        store: SourceChangeStore | None = None,
    ):
        self.registry = registry
        self.gateway = gateway
        self.store = store or NullSourceChangeStore()

    def observe_and_dispatch(
        self,
        source_id: str,
        *,
        version: str | None = None,
        content: bytes | str | None = None,
        content_hash: str | None = None,
        observed_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> AgentRunResult | None:
        source = self.registry.get(source_id)
        self.store.ensure_source(source)
        observation = self.registry.inspect(
            source_id,
            version=version,
            content=content,
            content_hash=content_hash,
            observed_at=observed_at,
            metadata=metadata,
        )
        if not observation.changed:
            return None
        if not self.store.accept_change(source, observation):
            # A durable store may already contain the revision after a
            # process restart; synchronise the in-process registry and skip
            # the duplicate dispatch.
            self.registry.commit(observation)
            return None
        self.registry.commit(observation)
        event = source_changed_event(observation, content=message)
        return self.gateway.dispatch(event)


__all__ = ["SourceWatcher"]
