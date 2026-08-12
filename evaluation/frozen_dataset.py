"""Structural admission checks for human-reviewed frozen evaluation data.

This validator deliberately does not score a model.  It verifies that a RAG or
intent-routing dataset has enough provenance and labels to be a meaningful
candidate for a production-tier evaluation manifest.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


VALID_SPLITS = {"regression", "validation", "test"}
VALID_FOCUSES = {"technical", "fundamental", "sentiment", "all", None}
VALID_TASK_INTENTS = {
    "investment_analysis",
    "backtest",
    "market_scan",
    "strategy_screen",
    "discussion",
    "trade_action",
    "clarify",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number} must be a JSON object")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path} has no cases")
    return rows


def _require_string(value: Any, label: str, errors: list[str], case_id: str) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{case_id}: missing {label}")


def _validate_provenance(row: dict[str, Any], errors: list[str], case_id: str) -> None:
    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        errors.append(f"{case_id}: missing provenance")
        return
    for field in ("origin", "reviewer", "reviewed_at"):
        _require_string(provenance.get(field), f"provenance.{field}", errors, case_id)


def validate_rag_rows(rows: list[dict[str, Any]], *, require_reviewed_provenance: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    split_counts: Counter[str] = Counter()

    for row in rows:
        case_id = str(row.get("id", ""))
        _require_string(row.get("id"), "id", errors, case_id or "<unknown>")
        if case_id in seen_ids:
            errors.append(f"{case_id}: duplicate id")
        seen_ids.add(case_id)
        query = row.get("query")
        _require_string(query, "query", errors, case_id)
        normalised_query = " ".join(query.lower().split()) if isinstance(query, str) else ""
        if normalised_query in seen_queries:
            errors.append(f"{case_id}: duplicate query")
        seen_queries.add(normalised_query)
        split = row.get("split")
        if split not in VALID_SPLITS:
            errors.append(f"{case_id}: invalid split {split!r}")
        else:
            split_counts[str(split)] += 1
        if not str(row.get("corpus_version", "")).startswith("sha256:"):
            errors.append(f"{case_id}: corpus_version must be SHA-256 pinned")
        _require_string(row.get("source_type"), "source_type", errors, case_id)
        if require_reviewed_provenance:
            _validate_provenance(row, errors, case_id)

        expected = row.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case_id}: missing expected object")
            continue
        relevant = expected.get("relevant_evidence_ids")
        citations = expected.get("required_citations")
        answer_facts = expected.get("answer_facts")
        abstain_allowed = expected.get("abstain_allowed")
        if not isinstance(answer_facts, list):
            errors.append(f"{case_id}: expected.answer_facts must be a list")
        if not isinstance(relevant, list) or not all(isinstance(item, str) and item for item in relevant):
            errors.append(f"{case_id}: expected.relevant_evidence_ids must be a string list")
        if not isinstance(citations, list):
            errors.append(f"{case_id}: expected.required_citations must be a list")
        elif relevant and not citations:
            errors.append(f"{case_id}: answerable case requires page-level citations")
        if not isinstance(abstain_allowed, bool):
            errors.append(f"{case_id}: expected.abstain_allowed must be boolean")

    return {
        "kind": "rag",
        "case_count": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "valid": not errors,
        "errors": errors,
    }


def validate_intent_rows(rows: list[dict[str, Any]], *, require_reviewed_provenance: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    intent_counts: Counter[int] = Counter()
    multi_intent_cases = 0

    for row in rows:
        case_id = str(row.get("id", ""))
        _require_string(row.get("id"), "id", errors, case_id or "<unknown>")
        if case_id in seen_ids:
            errors.append(f"{case_id}: duplicate id")
        seen_ids.add(case_id)
        query = row.get("query")
        _require_string(query, "query", errors, case_id)
        normalised_query = " ".join(query.lower().split()) if isinstance(query, str) else ""
        if normalised_query in seen_queries:
            errors.append(f"{case_id}: duplicate query")
        seen_queries.add(normalised_query)
        if require_reviewed_provenance:
            _validate_provenance(row, errors, case_id)

        expected = row.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case_id}: missing expected object")
            continue
        intent = expected.get("intent")
        if not isinstance(intent, int) or intent not in {1, 2, 3, 4}:
            errors.append(f"{case_id}: expected.intent must be 1, 2, 3, or 4")
        else:
            intent_counts[intent] += 1
        focus = expected.get("analyst_focus")
        if focus not in VALID_FOCUSES:
            errors.append(f"{case_id}: invalid analyst_focus {focus!r}")
        tasks = expected.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            errors.append(f"{case_id}: expected.tasks must be a non-empty list")
            continue
        multi_intent_cases += int(len(tasks) > 1)
        task_intents = set()
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                errors.append(f"{case_id}: task {index} must be an object")
                continue
            task_intent = task.get("intent")
            if task_intent not in VALID_TASK_INTENTS:
                errors.append(f"{case_id}: task {index} has invalid intent {task_intent!r}")
            task_intents.add(task_intent)
            dependencies = task.get("depends_on_intents", [])
            if not isinstance(dependencies, list) or any(item not in VALID_TASK_INTENTS for item in dependencies):
                errors.append(f"{case_id}: task {index} has invalid depends_on_intents")
            if task.get("requires_confirmation") is not None and not isinstance(task.get("requires_confirmation"), bool):
                errors.append(f"{case_id}: task {index} requires_confirmation must be boolean")
        if "trade_action" in task_intents and not any(task.get("requires_confirmation") is True for task in tasks if isinstance(task, dict)):
            errors.append(f"{case_id}: trade_action requires explicit confirmation")

    return {
        "kind": "intent",
        "case_count": len(rows),
        "intent_counts": {str(label): count for label, count in sorted(intent_counts.items())},
        "multi_intent_case_count": multi_intent_cases,
        "valid": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a frozen RAG or intent evaluation dataset")
    parser.add_argument("--kind", choices=("rag", "intent"), required=True)
    parser.add_argument(
        "--tier",
        choices=("contract", "smoke", "production"),
        default="production",
        help="Production requires reviewer provenance; smoke and contract validate structure only.",
    )
    parser.add_argument("--dataset", type=Path, required=True)
    args = parser.parse_args()
    rows = load_jsonl(args.dataset)
    require_reviewed_provenance = args.tier == "production"
    report = (
        validate_rag_rows(rows, require_reviewed_provenance=require_reviewed_provenance)
        if args.kind == "rag"
        else validate_intent_rows(rows, require_reviewed_provenance=require_reviewed_provenance)
    )
    report["tier"] = args.tier
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
