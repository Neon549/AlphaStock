"""Validate a Chinese, de-identified Gold-set export before production admission.

This is an intake contract, not a data generator and not an automatic labeler.
Rows may be supplied by the controlled export path only.  A valid intake still
needs the final immutable corpus hash, untouched test split and (for the
production flag) two independent reviewers before it can support a metric.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from evaluation.real_rag_test_admission import PII_PATTERNS, REAL_QUERY_ORIGINS


SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
REDACTION_VERSION = "alpha-stock-gold-redaction/v1"
VALID_SPLITS = {"train", "validation", "test"}
VALID_CATEGORIES = {
    "fact_query",
    "financial_report",
    "news_verification",
    "multi_stock_comparison",
    "high_risk_investment",
    "missing_information",
    "multi_turn_context",
    "compound_task",
}
FORBIDDEN_KEYS = {
    "actor_id", "account_id", "session_id", "request_id", "trace_id", "user_id",
    "user_email", "phone", "ip", "ip_address", "raw_trace", "raw_messages", "user_profile",
}


def _iter_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield from _iter_strings(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _iter_strings(nested, f"{path}[{index}]")


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(key).casefold() for key in value} | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value)) if value else set()
    return set()


def _iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        date.fromisoformat(value[:10])
        return True
    except ValueError:
        return False


def _iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _validate_review(row: dict[str, Any], errors: list[str], case_id: str, require_dual_review: bool) -> None:
    review = row.get("review")
    if not isinstance(review, dict):
        errors.append(f"{case_id}: review object is required")
        return
    reviewer = review.get("reviewer")
    reviewers = review.get("reviewers")
    names: list[str] = []
    if isinstance(reviewers, list):
        names = [str(item).strip() for item in reviewers if str(item).strip()]
        if len(names) != len(set(names)):
            errors.append(f"{case_id}: review.reviewers must be distinct")
    elif isinstance(reviewer, str) and reviewer.strip():
        names = [reviewer.strip()]
    else:
        errors.append(f"{case_id}: review.reviewer or review.reviewers is required")
    if require_dual_review and len(set(names)) < 2:
        errors.append(f"{case_id}: production admission requires two independent reviewers")
    if not _iso_datetime(review.get("reviewed_at")):
        errors.append(f"{case_id}: review.reviewed_at must be ISO-8601")
    if review.get("approved") is not True:
        errors.append(f"{case_id}: review.approved must be true for Gold admission")
    if review.get("arbitration_required") is True and not isinstance(review.get("arbitration"), dict):
        errors.append(f"{case_id}: arbitration details are required when arbitration_required=true")


def _validate_rag_expected(expected: dict[str, Any], errors: list[str], case_id: str) -> None:
    if not isinstance(expected.get("answer_facts"), list):
        errors.append(f"{case_id}: expected.answer_facts must be a list")
    evidence = expected.get("relevant_evidence_ids")
    if not isinstance(evidence, list) or not evidence or not all(isinstance(item, str) and item.strip() for item in evidence):
        errors.append(f"{case_id}: expected.relevant_evidence_ids must be a non-empty string list")
    citations = expected.get("required_citations")
    if not isinstance(citations, list):
        errors.append(f"{case_id}: expected.required_citations must be a list")
    else:
        for index, citation in enumerate(citations):
            if not isinstance(citation, dict) or not citation.get("evidence_id") or not isinstance(citation.get("page"), int) or citation["page"] <= 0:
                errors.append(f"{case_id}: citation {index} requires evidence_id and positive integer page")
    if not isinstance(expected.get("abstain_allowed"), bool):
        errors.append(f"{case_id}: expected.abstain_allowed must be boolean")


def _validate_intent_expected(expected: dict[str, Any], errors: list[str], case_id: str) -> None:
    if not isinstance(expected.get("intent"), str) or not expected["intent"].strip():
        errors.append(f"{case_id}: expected.intent is required")
    if not isinstance(expected.get("slots"), dict):
        errors.append(f"{case_id}: expected.slots must be an object")
    if not isinstance(expected.get("tasks"), list) or not expected["tasks"]:
        errors.append(f"{case_id}: expected.tasks must be a non-empty list")
    if not isinstance(expected.get("clarification_required"), bool):
        errors.append(f"{case_id}: expected.clarification_required must be boolean")
    if not isinstance(expected.get("abstain_allowed"), bool):
        errors.append(f"{case_id}: expected.abstain_allowed must be boolean")


def validate_gold_rows(
    rows: list[dict[str, Any]], *, kind: str = "rag", require_dual_review: bool = False,
    required_categories: set[str] | None = None,
) -> dict[str, Any]:
    """Return structural errors; never emits quality scores."""

    if kind not in {"rag", "intent"}:
        raise ValueError("kind must be rag or intent")
    errors: list[str] = []
    if not rows:
        errors.append("dataset must contain at least one row")
    seen_ids: set[str] = set()
    seen_queries: set[str] = set()
    split_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("row must be an object")
            continue
        case_id = str(row.get("id", "<unknown>"))
        if not isinstance(row.get("id"), str) or not row["id"].strip() or case_id in seen_ids:
            errors.append(f"{case_id}: missing or duplicate id")
        seen_ids.add(case_id)
        query = row.get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append(f"{case_id}: query is required")
        normalized = " ".join(query.casefold().split()) if isinstance(query, str) else ""
        if normalized in seen_queries:
            errors.append(f"{case_id}: duplicate normalized query")
        seen_queries.add(normalized)
        split = row.get("split")
        if split not in VALID_SPLITS:
            errors.append(f"{case_id}: split must be train, validation or test")
        else:
            split_counts[str(split)] += 1
        category = row.get("category")
        if category not in VALID_CATEGORIES:
            errors.append(f"{case_id}: invalid category {category!r}")
        else:
            category_counts[str(category)] += 1
        source = row.get("source")
        if not isinstance(source, dict):
            errors.append(f"{case_id}: source object is required")
        else:
            if source.get("origin") not in REAL_QUERY_ORIGINS:
                errors.append(f"{case_id}: source.origin must be deidentified_session or production_bad_case")
            if not SHA256.fullmatch(str(source.get("source_fingerprint", ""))):
                errors.append(f"{case_id}: source.source_fingerprint must be SHA-256")
            if not SHA256.fullmatch(str(source.get("corpus_version", ""))):
                errors.append(f"{case_id}: source.corpus_version must be SHA-256")
            if source.get("redaction_version") != REDACTION_VERSION:
                errors.append(f"{case_id}: source.redaction_version is required")
            if not _iso_date(source.get("collected_at")):
                errors.append(f"{case_id}: source.collected_at must be YYYY-MM-DD")
        _validate_review(row, errors, case_id, require_dual_review)
        expected = row.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case_id}: expected object is required")
        elif kind == "rag":
            _validate_rag_expected(expected, errors, case_id)
        else:
            _validate_intent_expected(expected, errors, case_id)
        forbidden = _all_keys(row) & FORBIDDEN_KEYS
        if forbidden:
            errors.append(f"{case_id}: forbidden identity/raw field(s): {sorted(forbidden)}")
        for path, text in _iter_strings({"query": row.get("query"), "source": row.get("source")}):
            for label, pattern in PII_PATTERNS:
                if pattern.search(text):
                    errors.append(f"{case_id}: possible {label} in {path}")

    required = required_categories or set()
    missing_categories = sorted(required - set(category_counts))
    errors.extend(f"missing required category: {category}" for category in missing_categories)
    if require_dual_review:
        for split in sorted(VALID_SPLITS - set(split_counts)):
            errors.append(f"production Gold requires non-empty split: {split}")
    return {
        "schema_version": "alpha-stock-production-gold-intake/v1",
        "kind": kind,
        "case_count": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "valid": not errors,
        "errors": errors,
        "production_ready": bool(rows) and not errors and require_dual_review,
        "claim_boundary": "valid 只表示字段、脱敏、来源、证据和复核元数据满足 intake 契约；只有不参与调参的冻结 test、双人独立复核、完整 corpus hash 和运行报告齐全后才可报告生产指标。",
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must be an object")
        rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate de-identified Chinese production Gold intake")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--kind", choices=("rag", "intent"), default="rag")
    parser.add_argument("--require-dual-review", action="store_true")
    parser.add_argument("--require-category", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = validate_gold_rows(
        _load_jsonl(args.dataset), kind=args.kind, require_dual_review=args.require_dual_review,
        required_categories=set(args.require_category),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
