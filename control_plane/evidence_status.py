"""Deterministic evidence-channel diagnostics for governed agent runs."""

from __future__ import annotations

from typing import Any


MARKET_TOOLS = frozenset({"market-price", "market-history", "financial-indicators"})


def _channel(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def build_evidence_status(
    *,
    rag_events: list[dict[str, Any]] | None = None,
    observations: list[dict[str, Any]] | None = None,
    validations: list[dict[str, Any]] | None = None,
    publish_status: str | None = None,
    publish_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Classify evidence execution without changing the publication decision."""

    rag_events = list(rag_events or [])
    observations = list(observations or [])
    validations = list(validations or [])
    retrievals = [item for item in rag_events if item.get("event") == "retrieval"]
    if not retrievals:
        document_rag = _channel(
            "not_requested", "no_document_rag_event",
            retrieval_count=0, retrieved_chunk_count=0, citation_count=0,
        )
    else:
        latest = retrievals[-1]
        retrieval_status = latest.get("status")
        chunks = int(latest.get("retrieved_chunk_count") or 0)
        if retrieval_status == "ok" and chunks > 0:
            status, reason = "success", "retrieval_hits_found"
        elif latest.get("abstain_reason") == "no_retrieval_hits":
            status, reason = "no_hit", "no_retrieval_hits"
        elif latest.get("abstain_reason") == "retrieval_error":
            status, reason = "error", "retrieval_error"
        elif retrieval_status == "abstained":
            status, reason = "no_hit", str(latest.get("abstain_reason") or "retrieval_abstained")
        else:
            status, reason = "error", str(retrieval_status or "retrieval_failed")
        document_rag = _channel(
            status, reason,
            retrieval_count=len(retrievals),
            retrieved_chunk_count=sum(int(item.get("retrieved_chunk_count") or 0) for item in retrievals),
            citation_count=sum(int(item.get("citation_count") or 0) for item in validations),
            rerank_applied=any(bool(item.get("rerank", {}).get("applied")) for item in retrievals),
        )

    market_observations = [
        item for item in observations
        if item.get("source_kind") == "market_evidence"
        or str(item.get("tool") or "") in MARKET_TOOLS
    ]
    market_successes = [item for item in market_observations if item.get("ok")]
    market_failures = [item for item in market_observations if not item.get("ok")]
    rejected = [
        item for item in market_successes
        if (item.get("freshness") or {}).get("status")
        in {"stale", "missing_report_period", "missing_retrieval_time"}
    ]
    if not market_observations:
        market_data = _channel("not_requested", "no_market_tool_attempt", attempt_count=0)
    elif market_successes and not rejected:
        market_data = _channel(
            "success", "market_evidence_received",
            attempt_count=len(market_observations), success_count=len(market_successes),
            failure_count=len(market_failures), rejected_count=0,
        )
    elif rejected and not market_failures:
        market_data = _channel(
            "stale_rejected", "market_evidence_rejected_by_freshness",
            attempt_count=len(market_observations), success_count=len(market_successes),
            failure_count=0, rejected_count=len(rejected),
        )
    elif market_failures and not market_successes:
        market_data = _channel(
            "error", "market_data_source_failed",
            attempt_count=len(market_observations), success_count=0,
            failure_count=len(market_failures), rejected_count=0,
        )
    else:
        market_data = _channel(
            "partial", "market_evidence_partially_available",
            attempt_count=len(market_observations), success_count=len(market_successes),
            failure_count=len(market_failures), rejected_count=len(rejected),
        )

    if not validations:
        citation_validation = _channel(
            "not_applicable", "no_citation_validation_event", validation_count=0,
        )
    else:
        failed = sum(item.get("status") == "failed" for item in validations)
        citation_validation = _channel(
            "failed" if failed else "passed",
            "citation_validation_failed" if failed else "citation_validation_passed",
            validation_count=len(validations), failure_count=failed,
            citation_count=sum(int(item.get("citation_count") or 0) for item in validations),
        )

    gate_map = {"blocked": "blocked", "requires_human_review": "human_review", "published": "passed"}
    gate_status = gate_map.get(publish_status or "", "not_applicable")
    output_gate = _channel(
        gate_status,
        "" if gate_status == "not_applicable" else (publish_status or gate_status),
        publish_reasons=list(publish_reasons or []),
    )
    return {
        "document_rag": document_rag,
        "market_data": market_data,
        "citation_validation": citation_validation,
        "output_gate": output_gate,
    }
