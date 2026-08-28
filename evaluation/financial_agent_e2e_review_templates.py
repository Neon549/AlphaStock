"""Create separate, hash-bound worksheets for two independent reviewers.

Templates intentionally contain no decisions. Reviewers fill their own file
without seeing the other review, then the normal review gate compares them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.financial_agent_e2e import load_jsonl, validate_cases
from evaluation.financial_agent_e2e_review import case_sha256


def build_review_template(cases: list[dict[str, Any]], reviewer_id: str) -> list[dict[str, Any]]:
    reviewer_id = reviewer_id.strip()
    if not reviewer_id:
        raise ValueError("reviewer_id is required")
    validation = validate_cases(cases)
    if not validation["valid"]:
        raise ValueError("invalid E2E cases: " + "; ".join(validation["errors"]))
    return [
        {
            "case_id": str(case["id"]),
            "case_sha256": case_sha256(case),
            "reviewer_id": reviewer_id,
            "role": "reviewer",
            "reviewed_at": None,
            "approved": None,
            "rubric_decisions": [
                {"id": str(rubric["id"]), "approved": None, "note": ""}
                for rubric in case["rubrics"]
            ],
            "allowed_evidence": [],
            "failure_taxonomy": [],
            "review_note": "",
        }
        for case in cases
    ]


def _write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create two independent E2E Gold review templates")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--reviewer-a", required=True)
    parser.add_argument("--reviewer-b", required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.reviewer_a.strip() == args.reviewer_b.strip():
        raise SystemExit("reviewer-a and reviewer-b must be distinct")
    cases = load_jsonl(args.cases)
    for label, reviewer in (("a", args.reviewer_a), ("b", args.reviewer_b)):
        _write_jsonl(build_review_template(cases, reviewer), args.out_dir / f"reviewer-{label}.jsonl")
    print(json.dumps({"cases": len(cases), "out_dir": str(args.out_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
