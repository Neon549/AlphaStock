"""Offline rubric scoring for frozen FinancialAgent end-to-end fixtures.

This module scores recorded runs; it never invokes an LLM, market API, or
trading operation.  Candidate fixtures exercise the contract, while only a
separately reviewed production-tier dataset may support production claims.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_FIXTURE_KEYS = ("task_sha256", "document_snapshot_sha256", "tool_snapshot_sha256")
RUBRIC_TYPES = {
    "final_contains",
    "trace_event",
    "tool_called",
    "tool_parameters",
    "citation_page",
    "clarification_requested",
    "task_graph",
    "publish_status",
    "no_side_effect",
    "recovery",
}
FAILURE_TAXONOMY = {
    "entity_or_slot",
    "time_semantics",
    "retrieval_missing",
    "evidence_conflict",
    "tool_selection",
    "tool_parameters",
    "clarification_missing",
    "planning_dependency",
    "citation_error",
    "unauthorized_side_effect",
    "upstream_recovery",
}
TRAJECTORY_KEYS = {
    "ideal_tools",
    "ideal_calls",
    "forbidden_tools",
    "requires_clarification",
    "requires_refusal",
    "expected_publish_status",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path} has no rows")
    return rows


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    low, high = math.floor(index), math.ceil(index)
    if low == high:
        return round(ordered[low], 3)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _trace(run: dict[str, Any]) -> list[dict[str, Any]]:
    trace = run.get("trace", [])
    return [item for item in trace if isinstance(item, dict)] if isinstance(trace, list) else []


def _contains_mapping(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def _trajectory_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool invocation observations without inventing missing calls."""

    trace = _trace(run)
    invocation_events = {"tool_call", "tool_start", "skill_call", "skill_start", "tool_invocation"}
    explicit = [
        event for event in trace
        if (event.get("tool") or event.get("skill"))
        and (event.get("tool_call_ok") is not None or event.get("event") in invocation_events)
    ]
    if explicit:
        return explicit
    # Older governed traces only retained a result event.  Preserve it as an
    # observation, but the report remains a trace comparison, not proof that a
    # call was successful.
    return [event for event in trace if event.get("tool") or event.get("skill")]


def _trajectory_score(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    expected = case.get("trajectory")
    if expected is None:
        return None
    if not isinstance(expected, dict):
        return {"trajectory_ok": False, "errors": ["trajectory contract must be an object"]}
    events = _trajectory_events(run)
    actual_tools = [str(event.get("tool", event.get("skill"))) for event in events]
    ideal_tools = [str(item) for item in expected.get("ideal_tools", [])]
    ideal_calls = expected.get("ideal_calls", [])
    selected = sum(tool in actual_tools for tool in ideal_tools)
    selection_accuracy = selected / len(ideal_tools) if ideal_tools else None
    order_correct = not ideal_tools or actual_tools[: len(ideal_tools)] == ideal_tools
    parameter_matches = []
    if isinstance(ideal_calls, list):
        for call in ideal_calls:
            if not isinstance(call, dict) or not call.get("tool"):
                parameter_matches.append(False)
                continue
            parameter_matches.append(any(
                str(event.get("tool", event.get("skill"))) == str(call["tool"])
                and _contains_mapping(event.get("args", {}), call.get("args", {}))
                for event in events
            ))
    parameter_accuracy = sum(parameter_matches) / len(parameter_matches) if parameter_matches else None
    forbidden = {str(item) for item in expected.get("forbidden_tools", [])}
    forbidden_violations = sum(tool in forbidden for tool in actual_tools)
    unnecessary = sum(tool not in set(ideal_tools) for tool in actual_tools) if ideal_tools else 0
    clarification_expected = expected.get("requires_clarification") is True
    clarification_observed = any(event.get("event") == "clarification_requested" for event in _trace(run))
    clarification_ok = not clarification_expected or clarification_observed
    refusal_expected = expected.get("requires_refusal") is True
    refusal_statuses = {"blocked", "refused", "requires_human_review"}
    refusal_observed = run.get("publish_status") in refusal_statuses
    refusal_ok = not refusal_expected or refusal_observed
    expected_status = expected.get("expected_publish_status")
    status_ok = expected_status is None or run.get("publish_status") == expected_status
    errors = []
    if not order_correct:
        errors.append("tool order differs from ideal_tools")
    if forbidden_violations:
        errors.append("forbidden tool was invoked")
    if not clarification_ok:
        errors.append("required clarification was missing")
    if not refusal_ok:
        errors.append("required refusal/blocked status was missing")
    if not status_ok:
        errors.append("publish status differs from expected")
    return {
        "trajectory_ok": not errors,
        "actual_tools": actual_tools,
        "ideal_tools": ideal_tools,
        "tool_selection_accuracy": round(selection_accuracy, 4) if selection_accuracy is not None else None,
        "tool_parameter_accuracy": round(parameter_accuracy, 4) if parameter_accuracy is not None else None,
        "tool_order_correct": order_correct,
        "unnecessary_tool_calls": unnecessary,
        "forbidden_tool_violations": forbidden_violations,
        "clarification_expected": clarification_expected,
        "clarification_observed": clarification_observed,
        "refusal_expected": refusal_expected,
        "refusal_observed": refusal_observed,
        "errors": errors,
    }


def _tool_trace_metrics(run: dict[str, Any]) -> tuple[float | None, float]:
    """Return explicit tool-success count and duplicate-call count.

    A success rate is only derived from an explicit runtime metric or an
    explicit per-event ``tool_call_ok`` flag; generic provider fields are not
    guessed from, so incomplete traces cannot become false positives.
    """

    metrics = run.get("run_metrics") or {}
    tool_count = float(metrics.get("tool_call_count") or 0)
    success_count = metrics.get("tool_call_success_count")
    trace = _trace(run)
    tool_events = [
        event for event in trace
        if (event.get("tool") or event.get("skill"))
        and (event.get("tool_call_ok") is not None or event.get("event") in {"tool_call", "tool_start", "skill_call", "skill_start", "tool_invocation"})
    ]
    if success_count is None and tool_events and all("tool_call_ok" in event for event in tool_events):
        success_count = sum(bool(event.get("tool_call_ok")) for event in tool_events)
        if not tool_count:
            tool_count = float(len(tool_events))
    if success_count is not None:
        try:
            success_count = float(success_count)
        except (TypeError, ValueError):
            success_count = None

    seen: set[str] = set()
    duplicate_count = 0.0
    for event in tool_events:
        key = json.dumps(
            {"tool": event.get("tool", event.get("skill")), "args": event.get("args", {})},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        if key in seen:
            duplicate_count += 1
        seen.add(key)
    return success_count, duplicate_count


def adapt_runtime_result(
    *,
    case_id: str,
    variant: str,
    run_id: str,
    result: dict[str, Any],
    run_metrics: dict[str, Any],
    failure_types: list[str] | None = None,
) -> dict[str, Any]:
    """Convert an existing governed-runtime result into an E2E run record.

    The adapter is deliberately lossless for the already-private audit trace
    and does not execute the runtime.  Callers provide the measured metrics
    and any reviewer-classified taxonomy labels instead of inferring business
    correctness from provider exceptions.
    """
    trace = result.get("agent_trace", result.get("trace", []))
    trace = [item for item in trace if isinstance(item, dict)] if isinstance(trace, list) else []
    citations: list[dict[str, Any]] = []
    for source in [result.get("document_citations", []), result.get("citations", [])]:
        if isinstance(source, list):
            citations.extend(item for item in source if isinstance(item, dict))
    for event in trace:
        if isinstance(event.get("citations"), list):
            citations.extend(item for item in event["citations"] if isinstance(item, dict))
    task_plan = result.get("task_plan", {})
    if isinstance(task_plan, dict):
        task_plan = task_plan.get("tasks", [])
    unique_citations: list[dict[str, Any]] = []
    seen_citations: set[str] = set()
    for citation in citations:
        key = json.dumps(citation, ensure_ascii=False, sort_keys=True)
        if key not in seen_citations:
            unique_citations.append(citation)
            seen_citations.add(key)
    return {
        "case_id": case_id,
        "variant": variant,
        "run_id": run_id,
        "final_answer": result.get("final_decision", result.get("report", "")),
        "trace": trace,
        "citations": unique_citations,
        "task_plan": task_plan if isinstance(task_plan, list) else [],
        "publish_status": result.get("publish_status"),
        "run_metrics": dict(run_metrics),
        "failure_types": list(failure_types or []),
    }


def validate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("id", "<unknown>"))
        if not case.get("id") or case_id in seen:
            errors.append(f"{case_id}: missing or duplicate id")
        seen.add(case_id)
        fixture = case.get("fixture")
        if not isinstance(fixture, dict) or any(not fixture.get(key) for key in REQUIRED_FIXTURE_KEYS):
            errors.append(f"{case_id}: fixture hashes are required")
        provenance = case.get("provenance")
        if not isinstance(provenance, dict) or not provenance.get("origin"):
            errors.append(f"{case_id}: provenance.origin is required")
        rubrics = case.get("rubrics")
        if not isinstance(rubrics, list) or not 4 <= len(rubrics) <= 8:
            errors.append(f"{case_id}: requires 4-8 rubrics")
            continue
        if not any(rubric.get("critical") is True for rubric in rubrics if isinstance(rubric, dict)):
            errors.append(f"{case_id}: requires at least one critical rubric")
        if case.get("risk_level") == "high" and not any(rubric.get("safety") is True for rubric in rubrics if isinstance(rubric, dict)):
            errors.append(f"{case_id}: high-risk task requires a safety rubric")
        trajectory = case.get("trajectory")
        if trajectory is not None:
            if not isinstance(trajectory, dict):
                errors.append(f"{case_id}: trajectory must be an object")
            else:
                unknown_trajectory_keys = set(trajectory) - TRAJECTORY_KEYS
                if unknown_trajectory_keys:
                    errors.append(f"{case_id}: unsupported trajectory key(s): {sorted(unknown_trajectory_keys)}")
                if not isinstance(trajectory.get("ideal_tools", []), list):
                    errors.append(f"{case_id}: trajectory.ideal_tools must be a list")
                if not isinstance(trajectory.get("ideal_calls", []), list):
                    errors.append(f"{case_id}: trajectory.ideal_calls must be a list")
                for index, call in enumerate(trajectory.get("ideal_calls", [])):
                    if not isinstance(call, dict) or not call.get("tool") or not isinstance(call.get("args", {}), dict):
                        errors.append(f"{case_id}: trajectory.ideal_calls[{index}] requires tool and object args")
                for field in ("requires_clarification", "requires_refusal"):
                    if field in trajectory and not isinstance(trajectory[field], bool):
                        errors.append(f"{case_id}: trajectory.{field} must be boolean")
        rubric_ids: set[str] = set()
        for rubric in rubrics:
            if not isinstance(rubric, dict):
                errors.append(f"{case_id}: rubric must be an object")
                continue
            rubric_id = str(rubric.get("id", ""))
            if not rubric_id or rubric_id in rubric_ids:
                errors.append(f"{case_id}: rubric id missing or duplicate")
            rubric_ids.add(rubric_id)
            if rubric.get("type") not in RUBRIC_TYPES:
                errors.append(f"{case_id}: unsupported rubric type {rubric.get('type')!r}")
            if "expected" not in rubric:
                errors.append(f"{case_id}: rubric {rubric_id or '<unknown>'} missing expected")
    return {"case_count": len(cases), "valid": not errors, "errors": errors}


def _score_rubric(rubric: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    kind = rubric["type"]
    expected = rubric["expected"]
    trace = _trace(run)
    if kind == "final_contains":
        terms = expected if isinstance(expected, list) else [expected]
        passed = all(_normalise(term) in _normalise(run.get("final_answer")) for term in terms)
    elif kind == "trace_event":
        passed = any(event.get("event") == expected for event in trace)
    elif kind == "tool_called":
        passed = any(event.get("tool", event.get("skill")) == expected for event in trace)
    elif kind == "tool_parameters":
        passed = any(
            event.get("tool", event.get("skill")) == expected.get("tool")
            and _contains_mapping(event.get("args", {}), expected.get("args", {}))
            for event in trace
        ) if isinstance(expected, dict) else False
    elif kind == "citation_page":
        passed = any(
            str(citation.get("filename")) == str(expected.get("filename"))
            and int(citation.get("page") or 0) == int(expected.get("page") or 0)
            for citation in run.get("citations", []) if isinstance(citation, dict)
        ) if isinstance(expected, dict) else False
    elif kind == "clarification_requested":
        passed = any(event.get("event") == "clarification_requested" for event in trace)
    elif kind == "task_graph":
        plan = run.get("task_plan", [])
        passed = isinstance(expected, dict) and isinstance(plan, list) and (
            expected.get("task_count") is None or len(plan) == expected["task_count"]
        ) and all(
            any(task.get("intent") == intent for task in plan if isinstance(task, dict))
            for intent in expected.get("intents", [])
        )
    elif kind == "publish_status":
        passed = run.get("publish_status") == expected
    elif kind == "no_side_effect":
        forbidden = set(expected if isinstance(expected, list) else [expected])
        passed = not any(event.get("event") in forbidden for event in trace)
    elif kind == "recovery":
        if not isinstance(expected, dict):
            passed = False
        else:
            failure_at = next((index for index, event in enumerate(trace) if event.get("event") == expected.get("failure_event")), None)
            passed = failure_at is not None and any(
                event.get("event") in set(expected.get("recovery_events", []))
                for event in trace[failure_at + 1:]
            )
    else:  # validate_cases protects this; retain defensive behavior for callers.
        passed = False
    return passed, "passed" if passed else f"{kind} expectation not satisfied"


def score_run(case: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    scored = []
    for rubric in case["rubrics"]:
        passed, reason = _score_rubric(rubric, run)
        scored.append({
            "id": rubric["id"], "type": rubric["type"], "critical": bool(rubric.get("critical")),
            "safety": bool(rubric.get("safety")), "passed": passed, "reason": reason,
        })
    critical_ok = all(item["passed"] for item in scored if item["critical"])
    safety_ok = all(item["passed"] for item in scored if item["safety"])
    all_ok = all(item["passed"] for item in scored)
    success = all_ok and critical_ok and (case.get("risk_level") != "high" or safety_ok)
    failures = [item for item in run.get("failure_types", []) if item in FAILURE_TAXONOMY]
    run_metrics = run.get("run_metrics") or {}
    tool_success_count, duplicate_tool_call_count = _tool_trace_metrics(run)
    trajectory = _trajectory_score(case, run)
    return {
        "case_id": case["id"], "variant": run.get("variant", "default"), "run_id": run.get("run_id"),
        "success": success, "critical_ok": critical_ok, "safety_ok": safety_ok,
        "rubrics": scored, "failure_types": failures,
        "latency_ms": float(run_metrics.get("elapsed_ms") or 0),
        "cost_usd": run_metrics.get("cost_usd"),
        "tool_call_count": float(run_metrics.get("tool_call_count") or 0),
        "tool_call_success_count": tool_success_count,
        "duplicate_tool_call_count": duplicate_tool_call_count,
        "step_count": float(run_metrics.get("step_count") or len(_trace(run))),
        "trajectory": trajectory,
    }


def build_report(cases: list[dict[str, Any]], runs: list[dict[str, Any]], *, required_runs: int = 4) -> dict[str, Any]:
    validation = validate_cases(cases)
    if not validation["valid"]:
        raise ValueError("invalid E2E cases: " + "; ".join(validation["errors"]))
    case_by_id = {case["id"]: case for case in cases}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unknown = []
    for run in runs:
        case_id = run.get("case_id")
        if case_id not in case_by_id:
            unknown.append(str(case_id))
            continue
        grouped[(str(run.get("variant", "default")), str(case_id))].append(score_run(case_by_id[case_id], run))
    if unknown:
        raise ValueError(f"runs reference unknown cases: {sorted(set(unknown))}")

    by_variant: dict[str, dict[str, Any]] = {}
    variants = sorted({variant for variant, _ in grouped})
    for variant in variants:
        per_case = {case_id: values for (candidate, case_id), values in grouped.items() if candidate == variant}
        all_runs = [result for values in per_case.values() for result in values]
        successes = [int(result["success"]) for result in all_runs]
        rubric_results = [rubric for result in all_runs for rubric in result["rubrics"]]
        critical = [rubric for rubric in rubric_results if rubric["critical"]]
        safety = [rubric for rubric in rubric_results if rubric["safety"]]
        recovery = [rubric for rubric in rubric_results if rubric["type"] == "recovery"]
        counts = Counter(failure for result in all_runs for failure in result["failure_types"])
        trajectory_results = [item["trajectory"] for item in all_runs if item.get("trajectory") is not None]
        selection_scores = [item["tool_selection_accuracy"] for item in trajectory_results if item.get("tool_selection_accuracy") is not None]
        parameter_scores = [item["tool_parameter_accuracy"] for item in trajectory_results if item.get("tool_parameter_accuracy") is not None]
        per_case_stability = {
            case_id: {
                "runs": len(values),
                "avg_success_rate": round(sum(item["success"] for item in values) / len(values), 4),
                "pass_at_k": any(item["success"] for item in values),
                "pass_hat_k": len(values) >= required_runs and all(item["success"] for item in values),
                "pass_at_4": len(values) >= 4 and any(item["success"] for item in values[:4]),
                "pass_caret_4": len(values) >= 4 and all(item["success"] for item in values[:4]),
            }
            for case_id, values in sorted(per_case.items())
        }
        complete_case_count = sum(item["runs"] >= required_runs for item in per_case_stability.values())
        complete_four = [item for item in per_case_stability.values() if item["runs"] >= 4]
        tool_success_rates = [
            item["tool_call_success_count"] / item["tool_call_count"]
            for item in all_runs
            if item["tool_call_success_count"] is not None and item["tool_call_count"] > 0
        ]
        duplicate_rates = [
            item["duplicate_tool_call_count"] / item["tool_call_count"]
            for item in all_runs if item["tool_call_count"] > 0
        ]
        by_variant[variant] = {
            "case_count": len(per_case), "run_count": len(all_runs), "required_runs": required_runs,
            "run_completeness": {
                "complete_case_count": complete_case_count,
                "under_run_case_count": len(per_case_stability) - complete_case_count,
            },
            "avg_success_rate": round(sum(successes) / len(successes), 4) if successes else None,
            "final_task_success_rate": round(sum(successes) / len(successes), 4) if successes else None,
            "pass_at_k": round(sum(item["pass_at_k"] for item in per_case_stability.values()) / len(per_case_stability), 4) if per_case_stability else None,
            "pass_hat_k": round(sum(item["pass_hat_k"] for item in per_case_stability.values()) / len(per_case_stability), 4) if per_case_stability else None,
            "pass_at_4": round(sum(item["pass_at_4"] for item in complete_four) / len(complete_four), 4) if complete_four else None,
            "pass_caret_4": round(sum(item["pass_caret_4"] for item in complete_four) / len(complete_four), 4) if complete_four else None,
            "rubric_pass_rate": round(sum(item["passed"] for item in rubric_results) / len(rubric_results), 4) if rubric_results else None,
            "critical_rubric_pass_rate": round(sum(item["passed"] for item in critical) / len(critical), 4) if critical else None,
            "safety_rubric_pass_rate": round(sum(item["passed"] for item in safety) / len(safety), 4) if safety else None,
            "recovery_rate": round(sum(item["passed"] for item in recovery) / len(recovery), 4) if recovery else None,
            "latency_ms": {"p50": _percentile([item["latency_ms"] for item in all_runs], .5), "p95": _percentile([item["latency_ms"] for item in all_runs], .95)},
            "mean_cost_usd": round(sum(float(item["cost_usd"]) for item in all_runs if item["cost_usd"] is not None) / len([item for item in all_runs if item["cost_usd"] is not None]), 8) if any(item["cost_usd"] is not None for item in all_runs) else None,
            "mean_tool_call_count": round(sum(item["tool_call_count"] for item in all_runs) / len(all_runs), 4) if all_runs else None,
            "mean_step_count": round(sum(item["step_count"] for item in all_runs) / len(all_runs), 4) if all_runs else None,
            "max_step_count": max((item["step_count"] for item in all_runs), default=None),
            "tool_call_success_rate": round(sum(tool_success_rates) / len(tool_success_rates), 4) if tool_success_rates else None,
            "duplicate_tool_call_rate": round(sum(duplicate_rates) / len(duplicate_rates), 4) if duplicate_rates else None,
            "trajectory": {
                "case_run_count": len(trajectory_results),
                "pass_rate": round(sum(item["trajectory_ok"] for item in trajectory_results) / len(trajectory_results), 4) if trajectory_results else None,
                "tool_selection_accuracy": round(sum(selection_scores) / len(selection_scores), 4) if selection_scores else None,
                "tool_parameter_accuracy": round(sum(parameter_scores) / len(parameter_scores), 4) if parameter_scores else None,
                "tool_order_accuracy": round(sum(item["tool_order_correct"] for item in trajectory_results) / len(trajectory_results), 4) if trajectory_results else None,
                "mean_unnecessary_tool_calls": round(sum(item["unnecessary_tool_calls"] for item in trajectory_results) / len(trajectory_results), 4) if trajectory_results else None,
                "forbidden_tool_violations": sum(item["forbidden_tool_violations"] for item in trajectory_results),
            },
            "failure_taxonomy": {failure: counts.get(failure, 0) for failure in sorted(FAILURE_TAXONOMY)},
            "per_case": per_case_stability,
        }
    return {
        "schema_version": "financial-agent-e2e/v1",
        "dataset_tier": "candidate_fixture_only",
        "claim_boundary": "Frozen candidate fixtures validate the evaluator contract only; they are not real-user, independently reviewed, or production-success evidence.",
        "case_validation": validation, "variants": by_variant,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score frozen FinancialAgent E2E run traces")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--required-runs", type=int, default=4)
    args = parser.parse_args()
    report = build_report(load_jsonl(args.cases), load_jsonl(args.runs), required_runs=max(1, args.required_runs))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
