#!/usr/bin/env python3
"""Approve, reject or label a captured Agent trajectory for post-training."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_learning.store import review_training_candidate


def _read_optional(path: Path | None) -> str | None:
    return path.read_text(encoding="utf-8") if path else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Human-review an Agent learning candidate")
    parser.add_argument("candidate_id")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--reject", action="store_true")
    parser.add_argument("--kind", choices=("sft", "dpo"))
    parser.add_argument("--chosen-file", type=Path)
    parser.add_argument("--rejected-file", type=Path)
    parser.add_argument("--instruction-file", type=Path)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    if not args.reject and (not args.kind or not args.chosen_file):
        parser.error("approval requires --kind and --chosen-file")
    if args.kind == "dpo" and not args.reject and not args.rejected_file:
        parser.error("DPO approval requires --rejected-file")

    result = review_training_candidate(
        args.candidate_id,
        approved=not args.reject,
        reviewer=args.reviewer,
        candidate_type=args.kind,
        chosen=_read_optional(args.chosen_file),
        rejected=_read_optional(args.rejected_file),
        instruction=_read_optional(args.instruction_file),
        review_note=args.note,
    )
    if result is None:
        print("candidate is missing or no longer pending_review")
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
