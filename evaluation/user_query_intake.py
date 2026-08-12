"""Validate and summarize user-provided AlphaStock evaluation-query intake.

The intake is deliberately broader than RAG.  Real user traffic includes
retrieval questions, incomplete messages, live-data requests and high-risk
decision requests.  This module keeps those lanes explicit instead of forcing
every utterance into a misleading Recall@K denominator.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from evaluation.frozen_dataset import load_jsonl
from evaluation.real_rag_test_admission import FORBIDDEN_KEYS, PII_PATTERNS


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = ROOT / "evaluation" / "datasets" / "user_query_intake_v1.jsonl"
VALID_LANES = {
    "rag_retrieval_after_clarification", "fresh_document_retrieval", "fresh_news_verification",
    "agent_governance", "agent_end_to_end", "multi_entity_governance", "clarification", "product_support",
}
VALID_ROUTES = {
    "investment_analysis", "clarify_then_investment_analysis", "clarify", "discussion",
    "source_refresh_then_retrieval", "clarify_then_source_verification", "clarify_then_comparison",
}
VALID_RISKS = {"low", "medium", "high"}


def validate_intake_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    lane_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()
    high_risk = 0
    for row in rows:
        case_id = str(row.get("id", "<unknown>"))
        query = row.get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append(f"{case_id}: missing query")
        if case_id in seen_ids:
            errors.append(f"{case_id}: duplicate id")
        seen_ids.add(case_id)
        key = "".join(str(query).casefold().split())
        if key in seen_queries:
            errors.append(f"{case_id}: duplicate query")
        seen_queries.add(key)
        lane = str(row.get("evaluation_lane", ""))
        route = str(row.get("runtime_route", ""))
        risk = str(row.get("risk_level", ""))
        if lane not in VALID_LANES:
            errors.append(f"{case_id}: invalid evaluation_lane {lane!r}")
        if route not in VALID_ROUTES:
            errors.append(f"{case_id}: invalid runtime_route {route!r}")
        if risk not in VALID_RISKS:
            errors.append(f"{case_id}: invalid risk_level {risk!r}")
        if not isinstance(row.get("requires_fresh_source"), bool):
            errors.append(f"{case_id}: requires_fresh_source must be boolean")
        if not isinstance(row.get("requires_human_review"), bool):
            errors.append(f"{case_id}: requires_human_review must be boolean")
        if not isinstance(row.get("missing_slots"), list):
            errors.append(f"{case_id}: missing_slots must be a list")
        origin = str(row.get("provenance", {}).get("origin", ""))
        if origin != "manual_expert_case":
            errors.append(f"{case_id}: simulated intake requires provenance.origin=manual_expert_case")
        for field in row:
            if field.casefold() in FORBIDDEN_KEYS:
                errors.append(f"{case_id}: forbidden identity field {field}")
        for label, pattern in PII_PATTERNS:
            if isinstance(query, str) and pattern.search(query):
                errors.append(f"{case_id}: possible {label} in query")
        lane_counts[lane] += 1
        route_counts[route] += 1
        high_risk += int(risk == "high")
    return {
        "dataset_tier": "manual_expert_query_candidates",
        "case_count": len(rows),
        "valid": not errors,
        "errors": errors,
        "lane_counts": dict(sorted(lane_counts.items())),
        "route_counts": dict(sorted(route_counts.items())),
        "high_risk_cases": high_risk,
        "claim_boundary": "Manual expert query candidates designed to resemble human requests; not a de-identified session sample, final RAG test or online-traffic metric.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AlphaStock user-query evaluation intake")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    report = validate_intake_rows(load_jsonl(args.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
