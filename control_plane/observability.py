"""Run-scoped telemetry shared by the runtime, LLM wrapper and audit store.

Provider calls buffer observations in a ContextVar. A run is inserted only
after the runtime returns, so model calls must not write audit rows directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import os
import re
from pathlib import Path
from time import perf_counter
from threading import Lock
from typing import Any, Iterator


@dataclass
class RunTelemetry:
    run_id: str
    started_at: float = field(default_factory=perf_counter)
    elapsed_ms: float = 0.0
    input_received: bool = False
    input_length: int = 0
    input_sha256: str | None = None
    run_metadata: dict[str, Any] = field(default_factory=dict)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    rag_events: list[dict[str, Any]] = field(default_factory=list)
    external_calls: list[dict[str, Any]] = field(default_factory=list)
    mcp_calls: list[dict[str, Any]] = field(default_factory=list)
    concurrency: int = 1
    final_metrics: dict[str, Any] | None = field(default=None, repr=False)
    lock: Lock = field(default_factory=Lock, repr=False)

    def summary(
        self,
        agent_trace: list[dict[str, Any]] | None = None,
        workflow_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        usage_fields = (
            "input_tokens", "output_tokens", "total_tokens",
            "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
        )
        totals = {field: 0 for field in usage_fields}
        models: dict[str, int] = {}
        llm_latency_ms = 0.0
        backup_calls = 0
        failed_calls = 0
        retry_calls = 0
        degraded_calls = 0
        circuit_open_calls = 0
        for call in self.llm_calls:
            model = str(call.get("model") or "unknown")
            models[model] = models.get(model, 0) + 1
            llm_latency_ms += float(call.get("latency_ms") or 0)
            backup_calls += int(bool(call.get("used_backup")))
            failed_calls += int(not bool(call.get("success")))
            retry_calls += int(str(call.get("recovery_action") or "").startswith("retry_"))
            degraded_calls += int(call.get("degradation_mode") == "draft_only")
            circuit_open_calls += int(call.get("failure_type") == "CIRCUIT_OPEN")
            for field in usage_fields:
                totals[field] += int(call.get(field) or 0)

        trace = agent_trace or []
        tool_events = [
            step for step in trace
            if step.get("event") in {"skill_result", "subagent_result", "tool_result"}
        ]
        tool_latency_ms = sum(float(step.get("latency_ms") or 0) for step in tool_events)
        retrievals = [event for event in self.rag_events if event.get("event") == "retrieval"]
        validations = [event for event in self.rag_events if event.get("event") == "citation_validation"]
        retrieved_chunk_count = sum(int(event.get("retrieved_chunk_count") or 0) for event in retrievals)
        failed_validations = sum(event.get("status") == "failed" for event in validations)
        provider_retry_attempted = retry_calls > 0
        provider_retry_succeeded = provider_retry_attempted and any(
            bool(call.get("success")) and int(call.get("attempt") or 1) > 1
            for call in self.llm_calls
        )
        tool_retry_attempted = any(
            int(step.get("attempts") or 0) > 1
            or any(item.get("event") == "retry_scheduled" for item in (step.get("retry_trace") or []))
            for step in tool_events
        )
        tool_failed = any(
            bool(step.get("tool_failure")) or str(step.get("status") or "").lower() == "failed"
            for step in tool_events
        ) or any(not bool(call.get("ok")) for call in [*self.external_calls, *self.mcp_calls])
        tool_retry_succeeded = tool_retry_attempted and any(
            not step.get("tool_failure")
            and str(step.get("status") or "completed").lower() not in {"failed", "error"}
            and (
                int(step.get("attempts") or 0) > 1
                or any(item.get("event") == "retry_scheduled" for item in (step.get("retry_trace") or []))
            )
            for step in tool_events
        )
        model_status = (
            "failed" if failed_calls and not any(call.get("success") for call in self.llm_calls)
            else "degraded" if backup_calls or degraded_calls
            else "ok" if self.llm_calls
            else "not_used"
        )
        from control_plane.evidence_status import build_evidence_status
        from control_plane.execution_status import build_execution_status

        evidence_status = build_evidence_status(
            rag_events=self.rag_events,
            observations=(workflow_result or {}).get("research_evidence") or [],
            validations=validations,
            publish_status=(workflow_result or {}).get("publish_status"),
            publish_reasons=(workflow_result or {}).get("publish_reasons") or [],
        )
        execution_status = build_execution_status(
            input_received=self.input_received,
            input_length=self.input_length,
            input_sha256=self.input_sha256,
            run_metadata=self.run_metadata,
            external_calls=self.external_calls,
            mcp_calls=self.mcp_calls,
        )
        from config.model_pricing import estimate_llm_cost

        cost = estimate_llm_cost(self.llm_calls)
        return {
            "schema_version": "run_metrics/v2",
            "run_id": self.run_id,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "concurrency": self.concurrency,
            "llm_call_count": len(self.llm_calls),
            "llm_success_count": sum(int(bool(call.get("success"))) for call in self.llm_calls),
            "llm_failure_count": failed_calls,
            "llm_backup_call_count": backup_calls,
            "llm_retry_call_count": retry_calls,
            "llm_draft_only_call_count": degraded_calls,
            "llm_circuit_open_count": circuit_open_calls,
            "llm_latency_ms": round(llm_latency_ms, 1),
            "tool_call_count": len(tool_events),
            "tool_latency_ms": round(tool_latency_ms, 1),
            "retrieval_count": len(retrievals),
            "retrieved_chunk_count": retrieved_chunk_count,
            "rerank_applied_count": sum(bool(event.get("rerank", {}).get("applied")) for event in retrievals),
            "abstained_retrieval_count": sum(event.get("status") == "abstained" for event in retrievals),
            "citation_validation_count": len(validations),
            "citation_validation_failure_count": failed_validations,
            "citation_count": sum(int(event.get("citation_count") or 0) for event in validations),
            "model_status": model_status,
            "provider_failed": failed_calls > 0,
            "tool_failed": tool_failed,
            "retry_attempted": provider_retry_attempted or tool_retry_attempted,
            "retry_succeeded": provider_retry_succeeded or tool_retry_succeeded,
            "fallback_used": backup_calls > 0,
            "models": models,
            "evidence_status": evidence_status,
            "execution_status": execution_status,
            **cost,
            **totals,
        }

    def langfuse_summary(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Send the canonical safe metrics contract to Langfuse."""
        return {
            "run_metrics": {
                key: value for key, value in metrics.items()
                if key not in {"evidence_status", "execution_status"}
            },
            **self.public_summary(metrics),
        }

    def public_summary(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Expose operational health without prompts, documents, scores or tokens."""
        validations = [event for event in self.rag_events if event.get("event") == "citation_validation"]
        validation_status = "not_applicable"
        if validations:
            validation_status = (
                "failed" if any(item.get("status") == "failed" for item in validations)
                else "passed" if any(item.get("status") == "passed" for item in validations)
                else "not_applicable"
            )
        return {
            "run_id": self.run_id,
            "elapsed_ms": metrics["elapsed_ms"],
            "retrieval_count": metrics["retrieval_count"],
            "retrieved_chunk_count": metrics["retrieved_chunk_count"],
            "citation_count": metrics["citation_count"],
            "citation_validation_status": validation_status,
            "abstained": bool(metrics["abstained_retrieval_count"]),
            "model_status": metrics["model_status"],
            "evidence_status": metrics.get("evidence_status", {}),
            "execution_status": metrics.get("execution_status", {}),
        }

    def export(self) -> dict[str, Any]:
        """Return raw audit rows without prompt text or provider credentials."""
        return {
            "llm_calls": [dict(call) for call in self.llm_calls],
            "tool_artifacts": {key: dict(value) for key, value in self.tool_artifacts.items()},
            "rag_events": [dict(event) for event in self.rag_events],
            "external_calls": [dict(event) for event in self.external_calls],
            "mcp_calls": [dict(event) for event in self.mcp_calls],
        }


_ACTIVE_RUN: ContextVar[RunTelemetry | None] = ContextVar("active_run_telemetry", default=None)
_CONCURRENCY_LOCK = Lock()
_ACTIVE_TELEMETRIES: dict[int, RunTelemetry] = {}


def _enter_concurrency(telemetry: RunTelemetry) -> None:
    with _CONCURRENCY_LOCK:
        _ACTIVE_TELEMETRIES[id(telemetry)] = telemetry
        active = len(_ACTIVE_TELEMETRIES)
        for item in _ACTIVE_TELEMETRIES.values():
            item.concurrency = max(item.concurrency, active)


def _exit_concurrency(telemetry: RunTelemetry) -> None:
    with _CONCURRENCY_LOCK:
        _ACTIVE_TELEMETRIES.pop(id(telemetry), None)


@contextmanager
def run_telemetry_scope(
    run_id: str,
    *,
    query: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[RunTelemetry]:
    raw_query = str(query or "")
    telemetry = RunTelemetry(
        run_id=run_id,
        input_received=bool(raw_query.strip()),
        input_length=len(raw_query),
        input_sha256=hashlib.sha256(raw_query.encode("utf-8")).hexdigest() if raw_query else None,
        run_metadata=dict(metadata or {}),
    )
    _enter_concurrency(telemetry)
    token = _ACTIVE_RUN.set(telemetry)
    _start_langfuse_run(run_id, query=query or "", metadata=metadata or {})
    try:
        yield telemetry
    finally:
        telemetry.elapsed_ms = (perf_counter() - telemetry.started_at) * 1000
        if telemetry.final_metrics is not None:
            # The completed metrics object is also referenced by the runtime
            # payload, so update the authoritative elapsed value in place.
            telemetry.final_metrics["elapsed_ms"] = round(telemetry.elapsed_ms, 1)
        metrics = telemetry.final_metrics or telemetry.summary()
        _finish_langfuse_run(run_id, telemetry.langfuse_summary(metrics))
        _ACTIVE_RUN.reset(token)
        _exit_concurrency(telemetry)


def current_run_id() -> str | None:
    telemetry = _ACTIVE_RUN.get()
    return telemetry.run_id if telemetry else None


def record_llm_call(
    *,
    model: str,
    latency_ms: float,
    success: bool,
    used_backup: bool,
    usage: dict[str, Any] | None,
    recovery: dict[str, Any] | None = None,
) -> None:
    telemetry = _ACTIVE_RUN.get()
    if telemetry is None:
        return
    with telemetry.lock:
        telemetry.llm_calls.append({
            "model": model,
            "latency_ms": round(float(latency_ms), 1),
            "success": bool(success),
            "used_backup": bool(used_backup),
            **(usage or {}),
            **(recovery or {}),
        })


def register_tool_artifact(result_ref: str, artifact: dict[str, Any]) -> None:
    telemetry = _ACTIVE_RUN.get()
    if telemetry is not None:
        with telemetry.lock:
            telemetry.tool_artifacts[result_ref] = artifact


def record_external_call(
    *,
    target: str,
    ok: bool,
    latency_ms: float,
    protocol: str = "https",
    error_type: str | None = None,
) -> None:
    """Record a bounded external provider call without URL/query contents."""

    telemetry = _ACTIVE_RUN.get()
    if telemetry is None:
        return
    with telemetry.lock:
        telemetry.external_calls.append({
            "target": str(target),
            "protocol": str(protocol),
            "ok": bool(ok),
            "latency_ms": round(float(latency_ms), 1),
            "error_type": str(error_type) if error_type else None,
        })


def record_mcp_call(*, target: str, ok: bool, latency_ms: float = 0.0, error_type: str | None = None) -> None:
    """Record an explicit MCP tool call when one occurs inside a run."""

    telemetry = _ACTIVE_RUN.get()
    if telemetry is None:
        return
    with telemetry.lock:
        telemetry.mcp_calls.append({
            "target": str(target),
            "ok": bool(ok),
            "latency_ms": round(float(latency_ms), 1),
            "error_type": str(error_type) if error_type else None,
        })


_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_NATIONAL_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_SECRET_VALUE = re.compile(r"(?i)\b(api[_ -]?key|token|secret|password)\s*[:=]\s*\S+")


def redact_query(query: str, *, preview_chars: int = 240) -> dict[str, Any]:
    """Create a bounded, non-reversible query representation for telemetry."""
    raw = str(query or "")
    preview = _EMAIL.sub("[email]", raw)
    preview = _PHONE.sub("[phone]", preview)
    preview = _NATIONAL_ID.sub("[id]", preview)
    preview = _SECRET_VALUE.sub(r"\1=[redacted]", preview)
    return {
        "query_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "query_length": len(raw),
        "query_preview": preview[:preview_chars],
        "query_truncated": len(preview) > preview_chars,
    }


def record_rag_event(event: str, payload: dict[str, Any]) -> None:
    """Buffer a sanitized RAG event and mirror it to the request Langfuse trace."""
    telemetry = _ACTIVE_RUN.get()
    if telemetry is None:
        return
    safe_payload = dict(payload)
    if isinstance(safe_payload.get("query"), str):
        safe_payload["query"] = redact_query(safe_payload["query"])
    safe_event = {"event": event, **safe_payload}
    with telemetry.lock:
        telemetry.rag_events.append(safe_event)
    _trace_langfuse_rag_event(telemetry.run_id, event, safe_event)


def _langfuse_enabled() -> bool:
    if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
        return True
    # The application loads .env in config.llm_config, but the root span starts
    # just before that module may first be imported. Detect configured keys
    # without logging or exporting their values.
    try:
        from dotenv import dotenv_values
        values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
        return bool(values.get("LANGFUSE_PUBLIC_KEY") and values.get("LANGFUSE_SECRET_KEY"))
    except Exception:
        return False


def _start_langfuse_run(run_id: str, *, query: str, metadata: dict[str, Any]) -> None:
    if not _langfuse_enabled():
        return
    try:
        from config.llm_config import start_langfuse_run_trace
        start_langfuse_run_trace(run_id, query=redact_query(query), metadata=metadata)
    except Exception:
        # Observability must never block a research run.
        return


def _finish_langfuse_run(run_id: str, summary: dict[str, Any]) -> None:
    if not _langfuse_enabled():
        return
    try:
        from config.llm_config import finish_langfuse_run_trace
        finish_langfuse_run_trace(run_id, summary=summary)
    except Exception:
        return


def _trace_langfuse_rag_event(run_id: str, event: str, payload: dict[str, Any]) -> None:
    if not _langfuse_enabled():
        return
    try:
        from config.llm_config import trace_langfuse_rag_event
        trace_langfuse_rag_event(run_id, event=event, payload=payload)
    except Exception:
        return
