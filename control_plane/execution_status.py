"""Execution-channel diagnostics for one governed agent run.

Evidence status answers whether usable evidence was obtained.  This module
answers a different question: which inputs and external transports were
actually exercised, and did those transport calls succeed?
"""

from __future__ import annotations

from typing import Any


def _channel(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def _aggregate_calls(calls: list[dict[str, Any]], *, not_requested_reason: str) -> dict[str, Any]:
    if not calls:
        return _channel("not_requested", not_requested_reason, attempt_count=0)
    successes = [item for item in calls if bool(item.get("ok"))]
    failures = [item for item in calls if not bool(item.get("ok"))]
    if successes and not failures:
        status, reason = "success", "all_calls_succeeded"
    elif failures and not successes:
        status, reason = "error", "all_calls_failed"
    else:
        status, reason = "partial", "some_calls_failed"
    return _channel(
        status,
        reason,
        attempt_count=len(calls),
        success_count=len(successes),
        failure_count=len(failures),
        targets=sorted({str(item.get("target") or "unknown") for item in calls}),
        latency_ms=round(sum(float(item.get("latency_ms") or 0) for item in calls), 1),
    )


def build_execution_status(
    *,
    input_received: bool = False,
    input_length: int = 0,
    input_sha256: str | None = None,
    run_metadata: dict[str, Any] | None = None,
    external_calls: list[dict[str, Any]] | None = None,
    mcp_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return privacy-safe status for user input and non-evidence transports."""

    metadata = run_metadata or {}
    if metadata.get("input_rejected"):
        user_input = _channel("rejected", "input_rejected_by_boundary")
    elif input_received:
        user_input = _channel(
            "received", "input_received",
            input_length=int(input_length or 0),
            input_sha256=input_sha256,
            trigger=str(metadata.get("trigger") or "unknown"),
            channel=str(metadata.get("channel") or "unknown"),
            session_document_attached=bool(metadata.get("has_session_document")),
        )
    else:
        user_input = _channel(
            "empty", "empty_input",
            input_length=0,
            trigger=str(metadata.get("trigger") or "unknown"),
            channel=str(metadata.get("channel") or "unknown"),
        )

    http_status = _aggregate_calls(
        list(external_calls or []),
        not_requested_reason="no_external_http_call",
    )

    mcp_attempts = list(mcp_calls or [])
    # A governed run entered through the MCP transport is itself an audited
    # MCP tool invocation.  This covers research_stock, while explicit
    # record_mcp_call() events cover future outbound MCP tools.
    if not mcp_attempts and (
        str(metadata.get("trigger") or "") == "mcp"
        or str(metadata.get("channel") or "") == "mcp"
    ):
        mcp_attempts = [{"target": metadata.get("operation") or "mcp_request", "ok": True}]
    mcp_status = _aggregate_calls(
        mcp_attempts,
        not_requested_reason="no_mcp_tool_call",
    )
    return {
        "user_input": user_input,
        "external_http": http_status,
        "mcp_tools": mcp_status,
    }
