"""Deterministic, run-level rubrics for the governed agent loop.

The evaluator is intentionally not an LLM judge.  It scores the execution
contract that is already observable in ``AgentRunResult``: route selection,
bounded execution, publication state and evidence-gate outcome.  Semantic
claim/evidence judging can be added as a separate, review-only stage later.
"""

from __future__ import annotations

import os
from hashlib import sha256
from typing import Any

from control_plane.contracts import AgentEvent, AgentRunResult


SCHEMA_VERSION = "agent-trace-eval/v1"
INVESTMENT_ROUTES = {"investment_agent_loop", "investment_workflow", "source_refresh"}
EXECUTION_FAILURE_EVENTS = {
    "invalid_planner_output",
    "invalid_subagent_request",
    "subagent_error",
    "skill_error",
    "tool_error",
    "permission_denied",
}
POLICY_EVENTS = {"skill_denied", "subagent_denied", "tool_denied"}


def flatten_trace(trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Flatten runtime trace entries and nested Harness entries.

    Runtime wraps harness records as ``{"event": "research_harness",
    "detail": {...}}``.  Keeping the wrapper and child gives evaluators a
    stable view without requiring the database to understand nested JSON.
    """

    flattened: list[dict[str, Any]] = []
    for item in trace or []:
        if not isinstance(item, dict):
            continue
        flattened.append(dict(item))
        nested = item.get("detail")
        if item.get("event") == "research_harness" and isinstance(nested, dict):
            child = dict(nested)
            child.setdefault("parent_event", "research_harness")
            flattened.append(child)
    return flattened


def _rubric(
    name: str,
    passed: bool,
    *,
    severity: str = "error",
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "severity": severity,
        "reasons": reasons or [],
    }


def _workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    workflow = payload.get("workflow_result")
    return workflow if isinstance(workflow, dict) else {}


def _event_names(trace: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("event")) for item in trace if item.get("event")}


def _badcase_type(event_name: str) -> str:
    if event_name in POLICY_EVENTS:
        return "policy_rejection"
    if event_name == "budget_exhausted":
        return "budget_exhausted"
    if event_name in EXECUTION_FAILURE_EVENTS:
        return "execution_error"
    return "execution_anomaly"


def evaluate_agent_run(event: AgentEvent, result: AgentRunResult) -> dict[str, Any]:
    """Evaluate one completed run using deterministic Agent rubrics.

    Outcomes have intentionally distinct meanings:

    * ``passed``: a governed investment draft reached human review, or a
      non-investment route completed without an execution anomaly.
    * ``safe_blocked``: the Output Gate correctly prevented an unsafe or
      unsupported investment draft from progressing.
    * ``failed``: execution, trace, policy or publication invariants failed.
    """

    payload = result.payload if isinstance(result.payload, dict) else {}
    workflow = _workflow_payload(payload)
    trace = flatten_trace(result.trace)
    event_names = _event_names(trace)
    route = str(result.route or "")
    publish_status = str(payload.get("publish_status") or workflow.get("publish_status") or "")
    human_review_required = bool(
        payload.get("human_review_required", workflow.get("human_review_required", False))
    )
    evidence_gate = workflow.get("evidence_gate")
    if not isinstance(evidence_gate, dict):
        evidence_gate = payload.get("evidence_gate") if isinstance(payload.get("evidence_gate"), dict) else {}

    is_duplicate = route == "duplicate"
    is_investment = route in INVESTMENT_ROUTES
    rubrics: list[dict[str, Any]] = []
    badcase_types: list[str] = []

    route_reasons: list[str] = []
    if not route:
        route_reasons.append("missing route")
    route_is_explicitly_blocked = route == "source_refresh" and "source_refresh_blocked" in event_names
    if not is_duplicate and route != "heartbeat" and not route_is_explicitly_blocked and "route_selected" not in event_names:
        route_reasons.append("missing route_selected trace event")
    rubrics.append(_rubric("route_traceability", not route_reasons, reasons=route_reasons))
    if route_reasons:
        badcase_types.append("missing_trace")

    anomaly_events = sorted(
        name for name in event_names if name in EXECUTION_FAILURE_EVENTS | POLICY_EVENTS | {"budget_exhausted"}
    )
    anomaly_reasons = [f"trace contains {name}" for name in anomaly_events]
    rubrics.append(_rubric("bounded_execution", not anomaly_reasons, reasons=anomaly_reasons))
    badcase_types.extend(_badcase_type(name) for name in anomaly_events)

    if is_investment:
        publication_reasons: list[str] = []
        if publish_status not in {"blocked", "requires_human_review"}:
            publication_reasons.append(f"unexpected publish status: {publish_status or 'missing'}")
        if publish_status == "requires_human_review" and not human_review_required:
            publication_reasons.append("human review status is missing its review requirement")
        rubrics.append(
            _rubric("publication_governance", not publication_reasons, reasons=publication_reasons)
        )
        if publication_reasons:
            badcase_types.append("publication_policy")

        evidence_reasons: list[str] = []
        if publish_status == "requires_human_review" and evidence_gate.get("passed") is not True:
            evidence_reasons.append("reviewable investment draft lacks a passed evidence gate")
        rubrics.append(_rubric("evidence_traceability", not evidence_reasons, reasons=evidence_reasons))
        if evidence_reasons:
            badcase_types.append("evidence_contract")
    else:
        rubrics.append(_rubric("publication_governance", True, severity="info"))
        rubrics.append(_rubric("evidence_traceability", True, severity="info"))

    failed_rubrics = [rubric for rubric in rubrics if not rubric["passed"] and rubric["severity"] == "error"]
    if failed_rubrics:
        outcome = "failed"
    elif is_investment and publish_status == "blocked":
        outcome = "safe_blocked"
        badcase_types.append("safe_output_block")
    else:
        outcome = "passed"

    deduplicated_badcases = list(dict.fromkeys(badcase_types))
    scored = [rubric for rubric in rubrics if rubric["severity"] != "info"]
    score = round(sum(1 for rubric in scored if rubric["passed"]) / max(len(scored), 1), 3)
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": result.run_id,
        "event_id": event.event_id,
        "route": route,
        "outcome": outcome,
        "score": score,
        "rubrics": rubrics,
        "badcase_types": deduplicated_badcases,
        "summary": {
            "trigger": event.trigger.value,
            "channel": event.channel,
            "publish_status": publish_status or None,
            "evidence_gate_passed": evidence_gate.get("passed"),
            "trace_event_count": len(trace),
            "tool_call_count": int((payload.get("run_metrics") or {}).get("tool_call_count") or 0),
        },
    }


def learning_capture_enabled(event: AgentEvent) -> bool:
    """Require explicit opt-in before retaining prompt text for training review."""

    if bool((event.metadata or {}).get("learning_capture")):
        return True
    return os.getenv("AGENT_LEARNING_CAPTURE", "false").strip().lower() in {"1", "true", "yes"}


def _trace_for_review(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep operational choices and evidence references, never full tool text."""

    keep = ("event", "step", "route", "skill", "subagent", "ok", "status", "result_ref", "latency_ms")
    reviewed: list[dict[str, Any]] = []
    for item in flatten_trace(trace):
        compact = {key: item[key] for key in keep if key in item}
        if compact:
            reviewed.append(compact)
    return reviewed


def build_learning_candidate(
    event: AgentEvent,
    result: AgentRunResult,
    evaluation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build a pending trajectory-review candidate, never an auto-labelled SFT/DPO row."""

    evaluation = evaluation or evaluate_agent_run(event, result)
    if evaluation.get("outcome") != "passed" or not learning_capture_enabled(event):
        return None

    payload = result.payload if isinstance(result.payload, dict) else {}
    workflow = _workflow_payload(payload)
    draft = str(payload.get("decision") or workflow.get("draft_decision") or workflow.get("final_decision") or "").strip()
    if not draft:
        return None

    digest = sha256(f"{result.run_id}:trajectory".encode("utf-8")).hexdigest()[:24]
    return {
        "candidate_id": f"trajectory-{digest}",
        "run_id": result.run_id,
        "candidate_type": "trajectory",
        "status": "pending_review",
        "sample": {
            "prompt": event.content,
            "draft": draft,
            "route": result.route,
            "trace": _trace_for_review(result.trace),
            "selected_skills": list(payload.get("selected_skills") or []),
            "evidence_refs": [
                card.get("result_ref")
                for card in (payload.get("evidence_cards") or [])
                if isinstance(card, dict) and card.get("result_ref")
            ],
        },
        "evaluation": evaluation,
    }
