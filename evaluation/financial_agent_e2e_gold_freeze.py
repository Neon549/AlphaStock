"""Freeze an admitted real-query E2E Gold package with immutable hashes."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from evaluation.financial_agent_e2e import load_jsonl
from evaluation.financial_agent_e2e_production_admission import build_production_admission_report
from evaluation.financial_agent_e2e_split import SPLIT_POLICY


DATASET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
VALID_SPLITS = {"train", "validation", "test"}


def _canonical_jsonl_hash(rows: list[dict[str, Any]]) -> str:
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _aggregate_hash(values: list[str]) -> str:
    return "sha256:" + hashlib.sha256("\n".join(sorted(values)).encode("utf-8")).hexdigest()


def build_gold_freeze_manifest(
    cases: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    *,
    dataset_id: str,
    frozen_at: str,
    train_separation: str,
    required_runs: int = 4,
    minimum_cases: int = 80,
) -> dict[str, Any]:
    errors: list[str] = []
    if not DATASET_ID.fullmatch(dataset_id):
        errors.append("dataset_id must be a stable lowercase identifier")
    try:
        datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        errors.append("frozen_at must be ISO-8601")
    if not train_separation.strip():
        errors.append("train_separation must describe evaluation isolation")
    if len(cases) < minimum_cases:
        errors.append(f"production Gold requires at least {minimum_cases} cases")

    split_counts = Counter(str(case.get("split", "")) for case in cases)
    invalid_splits = sorted(set(split_counts) - VALID_SPLITS)
    if invalid_splits:
        errors.append(f"invalid production split(s): {invalid_splits}")
    for split in sorted(VALID_SPLITS):
        if not split_counts.get(split):
            errors.append(f"production Gold requires non-empty {split} split")
    if any(case.get("split_policy") != SPLIT_POLICY for case in cases):
        errors.append(f"every case must use the pre-review split policy {SPLIT_POLICY}")

    admission = build_production_admission_report(
        cases, reviews, runs, required_runs=required_runs
    )
    if not admission["dataset_admission_ready"]:
        errors.append("E2E production admission is incomplete")

    document_snapshots = sorted({
        str(case.get("fixture", {}).get("document_snapshot_sha256")) for case in cases
    })
    tool_snapshots = sorted({
        str(case.get("fixture", {}).get("tool_snapshot_sha256")) for case in cases
    })
    runtime_snapshots = sorted({
        str(run.get("execution", {}).get("runtime_snapshot_sha256")) for run in runs
    })
    manifest = {
        "schema_version": "financial-agent-e2e-gold-freeze/v1",
        "dataset_id": dataset_id,
        "dataset_tier": "production" if not errors else "not_admitted",
        "frozen_at": frozen_at,
        "case_count": len(cases),
        "review_count": len(reviews),
        "run_count": len(runs),
        "required_runs": required_runs,
        "split_counts": {key: split_counts.get(key, 0) for key in sorted(VALID_SPLITS)},
        "train_separation": train_separation,
        "split_policy": SPLIT_POLICY,
        "artifacts": {
            "cases_sha256": _canonical_jsonl_hash(cases),
            "reviews_sha256": _canonical_jsonl_hash(reviews),
            "runs_sha256": _canonical_jsonl_hash(runs),
        },
        "snapshots": {
            "corpus_snapshot": _aggregate_hash(document_snapshots),
            "document_snapshots": document_snapshots,
            "tool_snapshots": tool_snapshots,
            "runtime_snapshots": runtime_snapshots,
        },
        "admission": admission,
        "valid": not errors,
        "errors": errors,
        "claim_boundary": (
            "A valid freeze proves immutable real-source cases, independent hash-bound review, "
            "split isolation and repeated controlled runs. Release permission remains a separate quality decision."
        ),
    }
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a reviewed FinancialAgent E2E Gold package")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--frozen-at", required=True)
    parser.add_argument("--train-separation", required=True)
    parser.add_argument("--minimum-cases", type=int, default=80)
    parser.add_argument("--required-runs", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build_gold_freeze_manifest(
        load_jsonl(args.cases), load_jsonl(args.reviews), load_jsonl(args.runs),
        dataset_id=args.dataset_id, frozen_at=args.frozen_at,
        train_separation=args.train_separation, minimum_cases=max(1, args.minimum_cases),
        required_runs=max(1, args.required_runs),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
