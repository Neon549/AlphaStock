"""Assign real E2E cases to stable splits before independent review begins."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluation.financial_agent_e2e import load_jsonl, validate_cases


SPLIT_POLICY = "financial-agent-e2e-split/v1:20-train:20-validation:60-test"


def assign_gold_splits(cases: list[dict[str, Any]], *, dataset_id: str) -> list[dict[str, Any]]:
    if len(cases) < 3:
        raise ValueError("at least three cases are required to create isolated splits")
    validation = validate_cases(cases)
    if not validation["valid"]:
        raise ValueError("invalid E2E cases: " + "; ".join(validation["errors"]))
    if any(case.get("split") not in {None, "", "review_queue"} for case in cases):
        raise ValueError("splits are already assigned; refusing to reshuffle reviewed data")

    rows = copy.deepcopy(cases)
    ordered = sorted(
        rows,
        key=lambda case: hashlib.sha256(
            (
                SPLIT_POLICY + ":" + dataset_id + ":"
                + str(case.get("provenance", {}).get("source_fingerprint", ""))
                + ":" + str(case["id"])
            ).encode("utf-8")
        ).hexdigest(),
    )
    count = len(ordered)
    train_count = max(1, round(count * 0.2))
    validation_count = max(1, round(count * 0.2))
    if train_count + validation_count >= count:
        validation_count = 1
        train_count = 1
    for index, case in enumerate(ordered):
        case["split"] = (
            "train" if index < train_count
            else "validation" if index < train_count + validation_count
            else "test"
        )
        case["split_policy"] = SPLIT_POLICY
    return sorted(ordered, key=lambda case: str(case["id"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign immutable pre-review E2E Gold splits")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = assign_gold_splits(load_jsonl(args.cases), dataset_id=args.dataset_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    counts = {split: sum(row["split"] == split for row in rows) for split in ("train", "validation", "test")}
    print(json.dumps({"dataset_id": args.dataset_id, "case_count": len(rows), "split_counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
