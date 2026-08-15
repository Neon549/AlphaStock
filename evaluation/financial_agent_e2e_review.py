"""Independent-review gate for FinancialAgent E2E tasks.

This is intentionally a review-state checker, not a human-review substitute.
It prevents a synthetic/public-source fixture from being promoted merely
because two copied review rows say "approved".
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from evaluation.financial_agent_e2e import FAILURE_TAXONOMY, load_jsonl
from evaluation.financial_agent_e2e_intake import REDACTION_VERSION, SHA256


VALID_ORIGINS = {"deidentified_session", "production_bad_case"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def has_admissible_provenance(case: dict[str, Any]) -> bool:
    """Check that review rows refer to an intake-produced real-source case.

    Origin alone is deliberately insufficient: a manual fixture could copy an
    allowed label.  The irreversible source fingerprint and redaction version
    are the link to the controlled export boundary.
    """

    provenance = case.get("provenance") or {}
    return (
        isinstance(provenance, dict)
        and provenance.get("origin") in VALID_ORIGINS
        and bool(SHA256.fullmatch(str(provenance.get("source_fingerprint", ""))))
        and provenance.get("redaction_version") == REDACTION_VERSION
    )


def validate_reviews(cases: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    case_ids = {str(case.get("id")) for case in cases}
    seen_pairs: set[tuple[str, str]] = set()
    for review in reviews:
        case_id = str(review.get("case_id", ""))
        reviewer = str(review.get("reviewer_id", ""))
        pair = (case_id, reviewer)
        if case_id not in case_ids:
            errors.append(f"{case_id}: review references an unknown case")
        if not reviewer:
            errors.append(f"{case_id}: missing reviewer_id")
        if pair in seen_pairs:
            errors.append(f"{case_id}: duplicate review from {reviewer}")
        seen_pairs.add(pair)
        if not isinstance(review.get("approved"), bool):
            errors.append(f"{case_id}: approved must be boolean")
        role = review.get("role", "reviewer")
        if role not in {"reviewer", "arbitrator"}:
            errors.append(f"{case_id}: role must be reviewer or arbitrator")
        if not isinstance(review.get("reviewed_at"), str) or not review.get("reviewed_at"):
            errors.append(f"{case_id}: missing reviewed_at")
        if not isinstance(review.get("rubric_decisions"), list) or not review["rubric_decisions"]:
            errors.append(f"{case_id}: rubric_decisions must be a non-empty list")
        if not isinstance(review.get("allowed_evidence"), list):
            errors.append(f"{case_id}: allowed_evidence must be a list")
        taxonomy = review.get("failure_taxonomy", [])
        if not isinstance(taxonomy, list) or any(item not in FAILURE_TAXONOMY for item in taxonomy):
            errors.append(f"{case_id}: invalid failure_taxonomy")
        if role == "arbitrator":
            resolution = review.get("resolution")
            if not isinstance(resolution, dict) or not isinstance(resolution.get("approved"), bool):
                errors.append(f"{case_id}: arbitrator requires resolution.approved")
            elif not isinstance(resolution.get("rubric_decisions"), list) or not isinstance(resolution.get("allowed_evidence"), list):
                errors.append(f"{case_id}: arbitrator resolution missing rubrics or evidence")
    return {"review_count": len(reviews), "valid": not errors, "errors": errors}


def build_review_report(cases: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    validation = validate_reviews(cases, reviews)
    if not validation["valid"]:
        raise ValueError("invalid E2E reviews: " + "; ".join(validation["errors"]))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for review in reviews:
        grouped.setdefault(str(review["case_id"]), []).append(review)

    statuses: Counter[str] = Counter()
    cases_out: list[dict[str, Any]] = []
    for case in cases:
        case_id = str(case["id"])
        case_reviews = grouped.get(case_id, [])
        primary = [review for review in case_reviews if review.get("role", "reviewer") == "reviewer"]
        arbiters = [review for review in case_reviews if review.get("role") == "arbitrator"]
        reviewers = {str(review["reviewer_id"]) for review in primary}
        admissible_provenance = has_admissible_provenance(case)
        status = "pending_review"
        if len(primary) >= 2 and len(reviewers) >= 2:
            pair = primary[:2]
            same_decision = pair[0]["approved"] == pair[1]["approved"]
            same_rubrics = _canonical(pair[0].get("rubric_decisions")) == _canonical(pair[1].get("rubric_decisions"))
            same_evidence = _canonical(pair[0].get("allowed_evidence")) == _canonical(pair[1].get("allowed_evidence"))
            same_taxonomy = _canonical(sorted(pair[0].get("failure_taxonomy", []))) == _canonical(sorted(pair[1].get("failure_taxonomy", [])))
            if not (same_decision and same_rubrics and same_evidence and same_taxonomy):
                if arbiters:
                    resolution = arbiters[-1]["resolution"]
                    if not resolution["approved"]:
                        status = "arbitrated_rejected"
                    elif not admissible_provenance:
                        status = "arbitrated_approved_not_admissible_source"
                    else:
                        status = "ready_for_production_admission"
                else:
                    status = "needs_arbitration"
            elif not pair[0]["approved"]:
                status = "consensus_rejected"
            elif not admissible_provenance:
                status = "consensus_approved_not_admissible_source"
            else:
                status = "ready_for_production_admission"
        statuses[status] += 1
        cases_out.append({"case_id": case_id, "review_count": len(case_reviews), "distinct_reviewers": len(reviewers), "arbitrator_count": len(arbiters), "status": status})
    return {
        "schema_version": "financial-agent-e2e-review/v1",
        "claim_boundary": "Review state is auditable workflow metadata. Only intake-produced deidentified real-query sources with a redaction version and irreversible source fingerprint, plus two independent matching reviews, can become eligible for separate production admission.",
        "review_validation": validation,
        "status_counts": dict(sorted(statuses.items())),
        "cases": cases_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate independent FinancialAgent E2E reviews")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=False)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reviews = load_jsonl(args.reviews) if args.reviews else []
    report = build_review_report(load_jsonl(args.cases), reviews)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
