"""A thin gateway: normalise, deduplicate and route; it never calls an LLM or tool."""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

from control_plane.contracts import AgentEvent, AgentRunResult
from control_plane.run_store import NullRunStore, RunStore


class AgentRuntime(Protocol):
    def run(self, event: AgentEvent) -> AgentRunResult: ...


class Gateway:
    """The entry boundary shared by HTTP now and other triggers later.

    Authentication and HTTP validation remain FastAPI concerns for this first
    phase.  This class has one responsibility: send a normalised event to an
    approved runtime and prevent the same event ID being dispatched twice in
    one process.
    """

    def __init__(self, runtime: AgentRuntime, store: RunStore | None = None, result_cache_size: int = 256):
        self._runtime = runtime
        self._store = store or NullRunStore()
        self._seen_event_ids: set[str] = set()
        self._completed_results: OrderedDict[str, AgentRunResult] = OrderedDict()
        self._result_cache_size = result_cache_size

    def dispatch(self, event: AgentEvent) -> AgentRunResult:
        if event.event_id in self._seen_event_ids:
            cached = self._completed_results.get(event.event_id)
            if cached is not None:
                return cached
            return AgentRunResult(
                run_id=event.event_id,
                route="duplicate",
                payload={"status": "duplicate", "event_id": event.event_id},
                trace=[{"event": "duplicate_event", "event_id": event.event_id}],
            )
        if not self._store.try_accept_event(event):
            return AgentRunResult(
                run_id=event.event_id,
                route="duplicate",
                payload={"status": "duplicate", "event_id": event.event_id},
                trace=[{"event": "duplicate_event", "event_id": event.event_id, "source": "persistent_store"}],
            )
        self._seen_event_ids.add(event.event_id)
        try:
            result = self._runtime.run(event)
            self._store.record_run(event, result)
            self._completed_results[event.event_id] = result
            self._completed_results.move_to_end(event.event_id)
            if len(self._completed_results) > self._result_cache_size:
                self._completed_results.popitem(last=False)
            return result
        except Exception as exc:
            self._store.record_failure(event, exc)
            raise
