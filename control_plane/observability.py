"""Run-scoped, privacy-safe Langfuse telemetry."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import os
import re
from time import perf_counter
from typing import Any, Iterator


@dataclass
class RunTelemetry:
    run_id: str
    started_at: float = field(default_factory=perf_counter)
    elapsed_ms: float = 0.0
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    rag_events: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        retrievals = [item for item in self.rag_events if item["event"] == "retrieval"]
        validations = [item for item in self.rag_events if item["event"] == "citation_validation"]
        failures = sum(not item["success"] for item in self.llm_calls)
        backup = sum(bool(item.get("used_backup")) for item in self.llm_calls)
        status = "failed" if failures and not any(item["success"] for item in self.llm_calls) else "degraded" if backup else "ok" if self.llm_calls else "not_used"
        return {
            "run_id": self.run_id, "elapsed_ms": round(self.elapsed_ms, 1),
            "llm_call_count": len(self.llm_calls), "model_status": status,
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in self.llm_calls),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in self.llm_calls),
            "retrieval_count": len(retrievals),
            "retrieved_chunk_count": sum(int(item.get("retrieved_chunk_count") or 0) for item in retrievals),
            "citation_count": sum(int(item.get("citation_count") or 0) for item in validations),
            "citation_validation_status": "failed" if any(item.get("status") == "failed" for item in validations) else "passed" if any(item.get("status") == "passed" for item in validations) else "not_applicable",
            "abstained": any(item.get("status") == "abstained" for item in retrievals),
        }


_ACTIVE: ContextVar[RunTelemetry | None] = ContextVar("active_run", default=None)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")


def redact_query(value: str) -> dict[str, Any]:
    raw = str(value or "")
    preview = _ID.sub("[id]", _PHONE.sub("[phone]", _EMAIL.sub("[email]", raw)))
    return {"query_sha256": hashlib.sha256(raw.encode()).hexdigest(), "query_length": len(raw), "query_preview": preview[:240], "query_truncated": len(preview) > 240}


@contextmanager
def run_telemetry_scope(run_id: str, *, query: str, metadata: dict[str, Any]) -> Iterator[RunTelemetry]:
    telemetry = RunTelemetry(run_id)
    token = _ACTIVE.set(telemetry)
    try:
        from config.llm_config import start_langfuse_run_trace
        start_langfuse_run_trace(run_id, query=redact_query(query), metadata=metadata)
    except Exception:
        pass
    try:
        yield telemetry
    finally:
        telemetry.elapsed_ms = (perf_counter() - telemetry.started_at) * 1000
        try:
            from config.llm_config import finish_langfuse_run_trace
            finish_langfuse_run_trace(run_id, summary=telemetry.summary())
        except Exception:
            pass
        _ACTIVE.reset(token)


def current_run_id() -> str | None:
    item = _ACTIVE.get()
    return item.run_id if item else None


def record_llm_call(*, model: str, latency_ms: float, success: bool, used_backup: bool, usage: dict | None) -> None:
    item = _ACTIVE.get()
    if item:
        item.llm_calls.append({"model": model, "latency_ms": round(latency_ms, 1), "success": success, "used_backup": used_backup, **(usage or {})})


def record_rag_event(event: str, payload: dict[str, Any]) -> None:
    item = _ACTIVE.get()
    if not item:
        return
    safe = dict(payload)
    if isinstance(safe.get("query"), str):
        safe["query"] = redact_query(safe["query"])
    event_data = {"event": event, **safe}
    item.rag_events.append(event_data)
    try:
        from config.llm_config import trace_langfuse_rag_event
        trace_langfuse_rag_event(item.run_id, event=event, payload=event_data)
    except Exception:
        pass
