"""Admit only already-deidentified real-query exports into E2E review.

This boundary intentionally refuses raw sessions, identifiers and PII.  It is
not a de-identification service: redaction must happen inside the controlled
production export path, before data reaches the repository or this command.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from evaluation.financial_agent_e2e import FAILURE_TAXONOMY, load_jsonl, validate_cases
from evaluation.real_rag_test_admission import FORBIDDEN_KEYS, PII_PATTERNS, REAL_QUERY_ORIGINS


STRICT_FORBIDDEN_KEYS = FORBIDDEN_KEYS | {
    "conversation_id", "request_id", "trace_id", "ip", "ip_address", "device_id",
    "raw_trace", "raw_messages", "user_profile", "account_id",
}
VALID_RISKS = {"low", "medium", "high"}
SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
REDACTION_VERSION = "financial-agent-e2e-redaction/v1"


def _iter_strings(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield from _iter_strings(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _iter_strings(nested, f"{path}[{index}]")


def _all_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key).casefold()
            yield from _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_keys(nested)


def validate_intake_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for row in rows:
        case_id = str(row.get("id", "<unknown>"))
        if not row.get("id") or case_id in seen_ids:
            errors.append(f"{case_id}: missing or duplicate id")
        seen_ids.add(case_id)
        if not isinstance(row.get("query"), str) or not row["query"].strip():
            errors.append(f"{case_id}: missing query")
        provenance = row.get("provenance", {})
        origin = provenance.get("origin") if isinstance(provenance, dict) else None
        if origin not in REAL_QUERY_ORIGINS:
            errors.append(f"{case_id}: origin must be deidentified_session or production_bad_case")
        if not isinstance(provenance, dict) or not SHA256.fullmatch(str(provenance.get("source_fingerprint", ""))):
            errors.append(f"{case_id}: provenance.source_fingerprint must be an irreversible SHA-256 fingerprint")
        if provenance.get("redaction_version") != REDACTION_VERSION:
            errors.append(f"{case_id}: requires current redaction_version")
        try:
            date.fromisoformat(str(row.get("collected_at", "")))
        except ValueError:
            errors.append(f"{case_id}: collected_at must be YYYY-MM-DD")
        if row.get("risk_level") not in VALID_RISKS:
            errors.append(f"{case_id}: invalid risk_level")
        fixture = row.get("fixture")
        if not isinstance(fixture, dict) or any(not SHA256.fullmatch(str(fixture.get(key, ""))) for key in ("document_snapshot_sha256", "tool_snapshot_sha256")):
            errors.append(f"{case_id}: document/tool snapshots must be SHA-256 pinned")
        rubrics = row.get("proposed_rubrics")
        candidate = {
            "id": case_id, "query": row.get("query"), "risk_level": row.get("risk_level"),
            "fixture": {
                "task_sha256": "sha256:" + "0" * 64,
                "document_snapshot_sha256": (fixture or {}).get("document_snapshot_sha256"),
                "tool_snapshot_sha256": (fixture or {}).get("tool_snapshot_sha256"),
            },
            "provenance": {"origin": origin or ""}, "rubrics": rubrics,
        }
        rubric_result = validate_cases([candidate])
        errors.extend(f"{case_id}: {error}" for error in rubric_result["errors"])
        taxonomy = row.get("observed_failure_taxonomy", [])
        if not isinstance(taxonomy, list) or any(item not in FAILURE_TAXONOMY for item in taxonomy):
            errors.append(f"{case_id}: invalid observed_failure_taxonomy")
        forbidden = set(_all_keys(row)) & STRICT_FORBIDDEN_KEYS
        if forbidden:
            errors.append(f"{case_id}: forbidden identity/raw field(s): {sorted(forbidden)}")
        for field_path, text in _iter_strings({key: row.get(key) for key in ("query", "collection_note", "provenance")}):
            for label, pattern in PII_PATTERNS:
                if pattern.search(text):
                    errors.append(f"{case_id}: possible {label} in {field_path}")
    return {
        "schema_version": "financial-agent-e2e-intake/v1", "case_count": len(rows), "valid": not errors,
        "errors": errors,
        "claim_boundary": "Accepted rows are review intake only. They remain unreviewed and cannot support production metrics until independent review, arbitration when needed, and repeated real runs complete.",
    }


def build_review_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    report = validate_intake_rows(rows)
    if not report["valid"]:
        raise ValueError("invalid real E2E intake: " + "; ".join(report["errors"]))
    cases: list[dict[str, Any]] = []
    for row in rows:
        source_id = str(row["id"])
        fixture = row["fixture"]
        cases.append({
            "id": f"e2e-real-intake-{source_id}", "parent_case_id": source_id, "split": "review_queue",
            "category": row.get("category", "real_query"), "risk_level": row["risk_level"], "query": row["query"],
            "fixture": {
                "task_sha256": "sha256:" + __import__("hashlib").sha256(f"financial-agent-e2e-intake-v1:{source_id}".encode()).hexdigest(),
                "document_snapshot_sha256": fixture["document_snapshot_sha256"], "tool_snapshot_sha256": fixture["tool_snapshot_sha256"],
            },
            "provenance": {
                "origin": row["provenance"]["origin"], "reviewer": "", "reviewed_at": "",
                "source_fingerprint": row["provenance"]["source_fingerprint"], "redaction_version": row["provenance"]["redaction_version"],
            },
            "observed_failure_taxonomy": row.get("observed_failure_taxonomy", []),
            "rubrics": row["proposed_rubrics"],
        })
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate already-deidentified FinancialAgent E2E intake")
    parser.add_argument("--intake", type=Path, required=True)
    parser.add_argument("--review-cases-out", type=Path)
    args = parser.parse_args()
    rows = load_jsonl(args.intake)
    report = validate_intake_rows(rows)
    if not report["valid"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    if args.review_cases_out:
        cases = build_review_cases(rows)
        content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in cases)
        args.review_cases_out.parent.mkdir(parents=True, exist_ok=True)
        args.review_cases_out.write_bytes(content.encode("utf-8"))
        report["review_case_count"] = len(cases)
        report["out"] = str(args.review_cases_out)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
