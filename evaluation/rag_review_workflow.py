"""Turn a pinned RAG candidate set into an auditable reviewed validation set.

This module deliberately cannot make a candidate set production-quality by
itself.  A human must approve every fact/citation pair.  It also defaults to
``validation`` because the current public-filing candidates have already been
used to compare retrievers; they are not an untouched final test set.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from evaluation.frozen_dataset import load_jsonl, validate_rag_rows


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_candidates.jsonl"


def build_review_template(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create one explicit approval task per candidate case."""

    return [
        {
            "case_id": case["id"],
            "decision": "pending",
            "reviewer": "",
            "reviewed_at": "",
            "approved_relevant_evidence_ids": case["expected"].get("relevant_evidence_ids", []),
            "approved_required_citations": case["expected"].get("required_citations", []),
            "note": "Confirm the metric, period, unit, evidence page and abstention condition before approval.",
        }
        for case in cases
    ]


def _require_non_empty_string(review: dict[str, Any], field: str, case_id: str, errors: list[str]) -> None:
    if not isinstance(review.get(field), str) or not review[field].strip():
        errors.append(f"{case_id}: review missing {field}")


def promote_reviewed_cases(
    cases: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    *,
    target_split: str = "validation",
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply human decisions, refusing partial, stale, or non-approved reviews."""

    errors: list[str] = []
    # These cases were used to select retrieval configurations.  Letting a
    # command-line flag relabel them as test would create a misleading metric.
    if target_split != "validation":
        return [], ["this reviewed candidate set may only be promoted to validation; build a new untouched test set"]
    by_case = {str(case["id"]): case for case in cases}
    by_review: dict[str, dict[str, Any]] = {}
    for review in reviews:
        case_id = str(review.get("case_id", ""))
        if case_id in by_review:
            errors.append(f"{case_id}: duplicate review")
        by_review[case_id] = review
    missing = sorted(set(by_case) - set(by_review))
    extra = sorted(set(by_review) - set(by_case))
    errors.extend(f"{case_id}: missing review" for case_id in missing)
    errors.extend(f"{case_id}: review has no candidate case" for case_id in extra)

    promoted: list[dict[str, Any]] = []
    for case_id, case in by_case.items():
        review = by_review.get(case_id)
        if not review:
            continue
        if review.get("decision") != "approved":
            errors.append(f"{case_id}: review decision must be approved")
            continue
        _require_non_empty_string(review, "reviewer", case_id, errors)
        _require_non_empty_string(review, "reviewed_at", case_id, errors)
        try:
            date.fromisoformat(str(review.get("reviewed_at", "")))
        except ValueError:
            errors.append(f"{case_id}: reviewed_at must be ISO YYYY-MM-DD")
        evidence_ids = review.get("approved_relevant_evidence_ids")
        citations = review.get("approved_required_citations")
        if not isinstance(evidence_ids, list) or not all(isinstance(item, str) and item for item in evidence_ids):
            errors.append(f"{case_id}: approved_relevant_evidence_ids must be a non-empty string list")
            continue
        if not isinstance(citations, list):
            errors.append(f"{case_id}: approved_required_citations must be a list")
            continue
        item = copy.deepcopy(case)
        item["split"] = target_split
        item["expected"]["relevant_evidence_ids"] = evidence_ids
        item["expected"]["required_citations"] = citations
        item["provenance"] = {
            "origin": "human_reviewed_public_filing_candidate",
            "reviewer": review["reviewer"].strip(),
            "reviewed_at": review["reviewed_at"].strip(),
        }
        item.setdefault("review", {})["note"] = str(review.get("note", ""))
        promoted.append(item)

    if not errors:
        validation = validate_rag_rows(promoted, require_reviewed_provenance=True)
        errors.extend(validation["errors"])
    return promoted, errors


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or promote auditable RAG candidate reviews")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--review-template-out", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--promoted-out", type=Path)
    parser.add_argument("--target-split", choices=("validation",), default="validation")
    args = parser.parse_args()
    if bool(args.review_template_out) == bool(args.reviews or args.promoted_out):
        parser.error("choose --review-template-out, or both --reviews and --promoted-out")
    cases = load_jsonl(args.cases)
    if args.review_template_out:
        _write_jsonl(args.review_template_out, build_review_template(cases))
        print(json.dumps({"template_cases": len(cases), "out": str(args.review_template_out)}, ensure_ascii=False))
        return 0
    if not args.reviews or not args.promoted_out:
        parser.error("--reviews and --promoted-out are required for promotion")
    promoted, errors = promote_reviewed_cases(cases, load_jsonl(args.reviews), target_split=args.target_split)
    if errors:
        print(json.dumps({"promoted": 0, "errors": errors}, ensure_ascii=False, indent=2))
        return 1
    _write_jsonl(args.promoted_out, promoted)
    print(json.dumps({"promoted": len(promoted), "split": args.target_split, "out": str(args.promoted_out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
