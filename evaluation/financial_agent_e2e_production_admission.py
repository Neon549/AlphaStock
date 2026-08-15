"""Production-tier admission gate for recorded FinancialAgent E2E runs.

This module does not invoke the agent.  It checks whether already recorded
controlled-runtime runs are eligible to be reported as a production E2E set.
Dataset admission deliberately retains failures; release quality is a separate
gate so an evaluator cannot hide unstable or unsafe cases by dropping them.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.financial_agent_e2e import FAILURE_TAXONOMY, build_report, load_jsonl, score_run, validate_cases
from evaluation.financial_agent_e2e_intake import REDACTION_VERSION, SHA256, STRICT_FORBIDDEN_KEYS
from evaluation.financial_agent_e2e_review import build_review_report, has_admissible_provenance


TRACE_REDACTION_VERSION = "financial-agent-e2e-trace-redaction/v1"


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value} | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()


def validate_controlled_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Reject synthetic-looking, duplicate, or non-auditable run records."""

    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for run in runs:
        case_id, variant, run_id = (str(run.get(key, "")) for key in ("case_id", "variant", "run_id"))
        label = f"{case_id or '<unknown>'}/{variant or '<unknown>'}/{run_id or '<unknown>'}"
        if not case_id or not variant or not run_id:
            errors.append(f"{label}: case_id, variant and run_id are required")
        key = (case_id, variant, run_id)
        if key in seen:
            errors.append(f"{label}: duplicate controlled run")
        seen.add(key)
        execution = run.get("execution")
        if not isinstance(execution, dict):
            errors.append(f"{label}: execution metadata is required")
        else:
            if not _valid_timestamp(execution.get("executed_at")):
                errors.append(f"{label}: execution.executed_at must be an ISO-8601 timestamp")
            if not SHA256.fullmatch(str(execution.get("runtime_snapshot_sha256", ""))):
                errors.append(f"{label}: execution.runtime_snapshot_sha256 must be SHA-256 pinned")
            if execution.get("trace_redaction_version") != TRACE_REDACTION_VERSION:
                errors.append(f"{label}: execution.trace_redaction_version is required")
        failure_types = run.get("failure_types", [])
        if not isinstance(failure_types, list) or any(item not in FAILURE_TAXONOMY for item in failure_types):
            errors.append(f"{label}: invalid failure_types")
        forbidden = _all_keys(run) & STRICT_FORBIDDEN_KEYS
        if forbidden:
            errors.append(f"{label}: forbidden identity/raw field(s): {sorted(forbidden)}")
    return {"run_count": len(runs), "valid": not errors, "errors": errors}


def build_production_admission_report(
    cases: list[dict[str, Any]], reviews: list[dict[str, Any]], runs: list[dict[str, Any]], *, required_runs: int = 4
) -> dict[str, Any]:
    """Report production-set eligibility and keep release safety distinct."""

    required_runs = max(1, required_runs)
    case_validation = validate_cases(cases)
    if not case_validation["valid"]:
        raise ValueError("invalid E2E cases: " + "; ".join(case_validation["errors"]))
    run_validation = validate_controlled_runs(runs)
    if not run_validation["valid"]:
        raise ValueError("invalid controlled E2E runs: " + "; ".join(run_validation["errors"]))
    review_report = build_review_report(cases, reviews)
    review_status = {item["case_id"]: item["status"] for item in review_report["cases"]}
    case_by_id = {str(case["id"]): case for case in cases}
    unknown_runs = sorted({str(run.get("case_id")) for run in runs if str(run.get("case_id")) not in case_by_id})
    if unknown_runs:
        raise ValueError(f"runs reference unknown cases: {unknown_runs}")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        grouped[(str(run["variant"]), str(run["case_id"]))].append(run)
    variants = sorted({variant for variant, _ in grouped})
    results: list[dict[str, Any]] = []
    for variant in variants:
        for case_id, case in case_by_id.items():
            entries = grouped.get((variant, case_id), [])
            scored = [score_run(case, entry) for entry in entries]
            review_ready = review_status.get(case_id) == "ready_for_production_admission"
            source_ready = has_admissible_provenance(case)
            runs_complete = len(entries) >= required_runs
            high_risk_safety_passed = case.get("risk_level") != "high" or (
                bool(scored) and all(item["safety_ok"] for item in scored)
            )
            all_critical_passed = bool(scored) and all(item["critical_ok"] for item in scored)
            dataset_eligible = source_ready and review_ready and runs_complete
            results.append({
                "case_id": case_id, "variant": variant, "risk_level": case.get("risk_level"),
                "review_status": review_status.get(case_id, "pending_review"),
                "source_ready": source_ready, "run_count": len(entries), "required_runs": required_runs,
                "runs_complete": runs_complete, "dataset_eligible": dataset_eligible,
                "high_risk_safety_passed": high_risk_safety_passed,
                "all_critical_rubrics_passed": all_critical_passed,
                "all_runs_successful": bool(scored) and all(item["success"] for item in scored),
            })
    dataset_admission_ready = bool(results) and all(item["dataset_eligible"] for item in results)
    release_gate_passed = dataset_admission_ready and all(
        item["high_risk_safety_passed"] and item["all_critical_rubrics_passed"] for item in results
    )
    return {
        "schema_version": "financial-agent-e2e-production-admission/v1",
        "dataset_tier": "production_e2e_eligible" if dataset_admission_ready else "not_admitted",
        "claim_boundary": "Dataset eligibility means real-source provenance, independent review, and repeated controlled-runtime execution are complete. It is not a claim that the agent is safe or successful; release_gate_passed remains a separate outcome.",
        "required_runs": required_runs,
        "case_validation": case_validation,
        "run_validation": run_validation,
        "review_status_counts": review_report["status_counts"],
        "dataset_admission_ready": dataset_admission_ready,
        "release_gate_passed": release_gate_passed,
        "variants": build_report(cases, runs, required_runs=required_runs)["variants"],
        "case_variant_results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Admit independently reviewed FinancialAgent E2E runs")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--required-runs", type=int, default=4)
    args = parser.parse_args()
    report = build_production_admission_report(
        load_jsonl(args.cases), load_jsonl(args.reviews), load_jsonl(args.runs), required_runs=max(1, args.required_runs)
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["dataset_admission_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
