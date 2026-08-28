"""Export already-controlled production queries into the E2E intake contract.

Run this on the production host, never against a copied raw database dump.
Only allow-listed fields leave the process. Human labels are supplied through
a separate file keyed by an HMAC fingerprint; this command never invents
rubrics, risk levels, or failure labels.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from evaluation.financial_agent_e2e_intake import (
    REDACTION_VERSION,
    STRICT_FORBIDDEN_KEYS,
    validate_intake_rows,
)
from evaluation.real_rag_test_admission import PII_PATTERNS


SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
SECRET_PATTERN = re.compile(r"(?i)\b(api[_ -]?key|token|secret|password)\s*[:=]\s*\S+")
EXPORT_REDACTION_VERSION = REDACTION_VERSION


def _all_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key).casefold()
            yield from _all_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _all_keys(nested)


def source_fingerprint(raw_query: str, export_key: str) -> str:
    """Use a production-only HMAC key; never hash a session/user identifier."""

    if not export_key:
        raise ValueError("ALPHASTOCK_EXPORT_FINGERPRINT_KEY is required")
    digest = hmac.new(export_key.encode("utf-8"), raw_query.encode("utf-8"), hashlib.sha256).hexdigest()
    return "sha256:" + digest


def redact_query(raw_query: str) -> str:
    """Replace common direct identifiers before the query enters the export."""

    value = SECRET_PATTERN.sub(r"\1=[REDACTED]", str(raw_query or ""))
    replacements = {
        "email": "[REDACTED_EMAIL]",
        "mainland_phone": "[REDACTED_PHONE]",
        "identity_number": "[REDACTED_ID]",
    }
    for label, pattern in PII_PATTERNS:
        value = pattern.sub(replacements[label], value)
    return value.strip()


def _load_labels(path: Path) -> dict[str, dict[str, Any]]:
    labels: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"labels line {line_number} must be an object")
        fingerprint = str(row.get("source_fingerprint", ""))
        if not SHA256.fullmatch(fingerprint) or fingerprint in labels:
            raise ValueError(f"labels line {line_number} has missing or duplicate source_fingerprint")
        forbidden = set(_all_keys(row)) & STRICT_FORBIDDEN_KEYS
        if forbidden:
            raise ValueError(f"labels line {line_number} contains forbidden fields: {sorted(forbidden)}")
        required = ("category", "risk_level", "proposed_rubrics", "observed_failure_taxonomy")
        if any(key not in row for key in required):
            raise ValueError(f"labels line {line_number} requires {', '.join(required)}")
        labels[fingerprint] = row
    return labels


def build_intake_rows(
    records: Iterable[Mapping[str, Any]],
    labels: Mapping[str, Mapping[str, Any]],
    *,
    export_key: str,
    document_snapshot: str,
    tool_snapshot: str,
    collected_at: str | None = None,
    max_cases: int = 120,
) -> list[dict[str, Any]]:
    if not SHA256.fullmatch(document_snapshot) or not SHA256.fullmatch(tool_snapshot):
        raise ValueError("document_snapshot and tool_snapshot must be sha256:<64 hex>")
    collected = collected_at or date.today().isoformat()
    try:
        date.fromisoformat(collected)
    except ValueError as exc:
        raise ValueError("collected_at must be YYYY-MM-DD") from exc

    grouped: dict[str, Mapping[str, Any]] = {}
    for record in records:
        raw_query = str(record.get("query") or "").strip()
        if not raw_query:
            continue
        fingerprint = source_fingerprint(raw_query, export_key)
        completed = record.get("completed_at")
        previous = grouped.get(fingerprint)
        if previous is None or str(completed or "") > str(previous.get("completed_at") or ""):
            grouped[fingerprint] = {"query": raw_query, "completed_at": completed}

    if len(grouped) > max_cases:
        # Deterministically keep the most recent labelled cases, never an
        # arbitrary database order. Missing labels are still reported below.
        ordered = sorted(grouped.items(), key=lambda item: str(item[1].get("completed_at") or ""), reverse=True)
        grouped = dict(ordered[:max_cases])

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for fingerprint, record in sorted(grouped.items()):
        label = labels.get(fingerprint)
        if label is None:
            errors.append(f"missing human label for {fingerprint}")
            continue
        forbidden = set(_all_keys(label)) & STRICT_FORBIDDEN_KEYS
        if forbidden:
            errors.append(f"human label for {fingerprint} contains forbidden fields: {sorted(forbidden)}")
            continue
        if str(label.get("source_fingerprint")) != fingerprint:
            errors.append(f"human label fingerprint mismatch for {fingerprint}")
            continue
        query = redact_query(str(record["query"]))
        if not query:
            errors.append(f"empty query after redaction for {fingerprint}")
            continue
        rows.append({
            "id": "real-" + fingerprint.removeprefix("sha256:")[:20],
            "query": query,
            "collected_at": collected,
            "category": label["category"],
            "risk_level": label["risk_level"],
            "fixture": {
                "document_snapshot_sha256": document_snapshot,
                "tool_snapshot_sha256": tool_snapshot,
            },
            "provenance": {
                "origin": "deidentified_session",
                "source_fingerprint": fingerprint,
                "redaction_version": EXPORT_REDACTION_VERSION,
            },
            "observed_failure_taxonomy": list(label["observed_failure_taxonomy"]),
            "proposed_rubrics": list(label["proposed_rubrics"]),
        })
    if errors:
        raise ValueError("; ".join(errors))
    report = validate_intake_rows(rows)
    if not report["valid"]:
        raise ValueError("exported rows failed intake validation: " + "; ".join(report["errors"]))
    return rows


def build_label_template(
    records: Iterable[Mapping[str, Any]], *, export_key: str, collected_at: str | None = None,
    max_cases: int = 120,
) -> list[dict[str, Any]]:
    """Create a safe worksheet containing only de-identified text and HMACs."""

    grouped: dict[str, Mapping[str, Any]] = {}
    for record in records:
        raw_query = str(record.get("query") or "").strip()
        if not raw_query:
            continue
        fingerprint = source_fingerprint(raw_query, export_key)
        previous = grouped.get(fingerprint)
        if previous is None or str(record.get("completed_at") or "") > str(previous.get("completed_at") or ""):
            grouped[fingerprint] = {"query": raw_query, "completed_at": record.get("completed_at")}
    ordered = sorted(grouped.items(), key=lambda item: str(item[1].get("completed_at") or ""), reverse=True)[:max_cases]
    collected = collected_at or date.today().isoformat()
    return [
        {
            "source_fingerprint": fingerprint,
            "query": redact_query(str(record["query"])),
            "collected_at": collected,
            "category": "",
            "risk_level": "",
            "observed_failure_taxonomy": [],
            "proposed_rubrics": [],
            "reviewer_note": "",
        }
        for fingerprint, record in ordered
    ]


def _read_production_records() -> list[dict[str, Any]]:
    """Read only the user query and completion time; never select identity columns."""

    from db import get_conn

    with get_conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT transcript.content, runs.completed_at
                FROM agent_runs AS runs
                JOIN agent_session_transcript AS transcript
                  ON transcript.run_id = runs.run_id
                WHERE transcript.role = 'user'
                  AND transcript.content IS NOT NULL
                ORDER BY runs.completed_at DESC
                """
            )
            rows = [{"query": item[0], "completed_at": item[1].isoformat() if item[1] else ""} for item in cursor.fetchall()]
        conn.rollback()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Export controlled, de-identified E2E intake JSONL")
    parser.add_argument("--labels", type=Path, help="Completed human labels keyed by HMAC source_fingerprint")
    parser.add_argument("--label-template-out", type=Path, help="Write a safe worksheet for human labeling, without producing intake")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--document-snapshot", default=os.getenv("ALPHASTOCK_GOLD_DOCUMENT_SNAPSHOT", ""))
    parser.add_argument("--tool-snapshot", default=os.getenv("ALPHASTOCK_GOLD_TOOL_SNAPSHOT", ""))
    parser.add_argument("--collected-at", default=None)
    parser.add_argument("--max-cases", type=int, default=120)
    args = parser.parse_args()
    try:
        if bool(args.labels) == bool(args.label_template_out):
            raise ValueError("provide exactly one of --label-template-out or --labels")
        records = _read_production_records()
        export_key = os.getenv("ALPHASTOCK_EXPORT_FINGERPRINT_KEY", "")
        if args.label_template_out:
            rows = build_label_template(
                records, export_key=export_key, collected_at=args.collected_at,
                max_cases=max(1, args.max_cases),
            )
            args.label_template_out.parent.mkdir(parents=True, exist_ok=True)
            args.label_template_out.write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
            print(json.dumps({"label_template_count": len(rows), "out": str(args.label_template_out)}, ensure_ascii=False))
            return 0
        labels = _load_labels(args.labels)
        rows = build_intake_rows(
            records, labels, export_key=export_key,
            document_snapshot=args.document_snapshot, tool_snapshot=args.tool_snapshot,
            collected_at=args.collected_at, max_cases=max(1, args.max_cases),
        )
    except Exception as exc:
        print(f"controlled export refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps({"case_count": len(rows), "out": str(args.out), "redaction_version": EXPORT_REDACTION_VERSION}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
