"""Audit a de-identified, untouched RAG final-test set before it is frozen.

It is intentionally stricter than the generic frozen-dataset validator:
final-test cases must originate from real de-identified traffic or a documented
production bad case, and may not overlap the retriever-selection datasets by
query, source document or labelled fact.  This is a guardrail against turning
rewritten validation questions into an impressive-but-invalid test score.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

from evaluation.frozen_dataset import load_jsonl, validate_rag_rows


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REFERENCE_DATASETS = (
    ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_candidates.jsonl",
    ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_query_variants.jsonl",
)
REAL_QUERY_ORIGINS = {"deidentified_session", "production_bad_case"}
FORBIDDEN_KEYS = {"actor_id", "session_id", "user_id", "user_email", "raw_trace_id", "phone"}
PII_PATTERNS = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("mainland_phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("identity_number", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
)


def _normalise(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value).casefold())


def _iter_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield from _iter_strings(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _iter_strings(nested, f"{path}[{index}]")


def _free_text_fields(row: dict[str, Any]) -> Iterable[tuple[str, str]]:
    """Only inspect user-entered/free-text fields for PII.

    Financial answer values regularly look like eleven-digit mainland phone
    numbers. Evidence identifiers, numerical answer facts and citations are
    controlled evaluation labels, so scanning them creates false positives
    without improving query de-identification.
    """

    for field in ("query", "review", "collection_note"):
        if field in row:
            yield from _iter_strings(row[field], f"$.{field}")
    if "provenance" in row:
        yield from _iter_strings(row["provenance"], "$.provenance")


def _citation_documents(row: dict[str, Any]) -> set[str]:
    citations = row.get("expected", {}).get("required_citations", [])
    return {
        str(citation.get("filename", "")).casefold()
        for citation in citations
        if isinstance(citation, dict) and citation.get("filename")
    }


def _fact_keys(row: dict[str, Any]) -> set[tuple[str, str, str]]:
    documents = _citation_documents(row) or {"<uncited-abstention>"}
    facts = row.get("expected", {}).get("answer_facts", [])
    return {
        (document, _normalise(fact.get("name")), _normalise(fact.get("value")))
        for document in documents
        for fact in facts
        if isinstance(fact, dict)
    }


def audit_final_test_rows(
    rows: list[dict[str, Any]],
    reference_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return admission errors; no score is emitted from an unadmitted set."""

    errors: list[str] = []
    baseline = validate_rag_rows(rows, require_reviewed_provenance=True)
    errors.extend(baseline["errors"])
    reference_queries = {_normalise(row.get("query")) for row in reference_rows}
    reference_documents = set().union(*(_citation_documents(row) for row in reference_rows)) if reference_rows else set()
    reference_facts = set().union(*(_fact_keys(row) for row in reference_rows)) if reference_rows else set()

    for row in rows:
        case_id = str(row.get("id", "<unknown>"))
        if row.get("split") != "test":
            errors.append(f"{case_id}: final test requires split=test")
        origin = str(row.get("provenance", {}).get("origin", ""))
        if origin not in REAL_QUERY_ORIGINS:
            errors.append(f"{case_id}: origin must be one of {sorted(REAL_QUERY_ORIGINS)}")
        if _normalise(row.get("query")) in reference_queries:
            errors.append(f"{case_id}: query overlaps a retriever-selection dataset")
        shared_documents = _citation_documents(row) & reference_documents
        if shared_documents:
            errors.append(f"{case_id}: source document overlaps a retriever-selection dataset: {sorted(shared_documents)}")
        shared_facts = _fact_keys(row) & reference_facts
        if shared_facts:
            errors.append(f"{case_id}: labelled fact overlaps a retriever-selection dataset")
        for key in row:
            if key.casefold() in FORBIDDEN_KEYS:
                errors.append(f"{case_id}: forbidden identity field {key}")
        for field_path, text in _free_text_fields(row):
            for label, pattern in PII_PATTERNS:
                if pattern.search(text):
                    errors.append(f"{case_id}: possible {label} in {field_path}")
    return {
        "kind": "rag_final_test_admission",
        "case_count": len(rows),
        "reference_case_count": len(reference_rows),
        "valid": not errors,
        "errors": errors,
        "policy": {
            "allowed_origins": sorted(REAL_QUERY_ORIGINS),
            "blocked_overlap": ["normalised_query", "citation_document", "labelled_fact"],
            "pii_scan": [label for label, _ in PII_PATTERNS],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit an untouched de-identified RAG final test set")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    references = args.reference or list(DEFAULT_REFERENCE_DATASETS)
    report = audit_final_test_rows(
        load_jsonl(args.dataset),
        [row for reference in references for row in load_jsonl(reference)],
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
