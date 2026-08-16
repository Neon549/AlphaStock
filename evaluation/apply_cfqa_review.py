"""Apply a non-Gold CFQA visual-repair manifest to a derived JSONL copy."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def apply_repairs(
    cases: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a derived copy and never mutate the input cases."""

    if manifest.get("promotion_eligible") is not False or manifest.get("human_review_required") is not True:
        raise ValueError("CFQA repair manifest must remain non-Gold and require independent human review")

    by_case = {str(case.get("id")): case for case in cases}
    by_evidence = {str(chunk.get("evidence_id")): chunk for chunk in chunks}
    output = copy.deepcopy(cases)
    output_by_case = {str(case.get("id")): case for case in output}
    seen: set[str] = set()

    for repair in manifest.get("repairs", []):
        case_id = str(repair.get("case_id", ""))
        if not case_id or case_id in seen:
            raise ValueError(f"duplicate or missing repair case_id: {case_id!r}")
        seen.add(case_id)
        if case_id not in by_case:
            raise ValueError(f"repair case does not exist: {case_id}")
        evidence_ids = [str(value) for value in repair.get("evidence_ids", [])]
        if not evidence_ids or any(value not in by_evidence for value in evidence_ids):
            missing = [value for value in evidence_ids if value not in by_evidence]
            raise ValueError(f"repair evidence does not exist for {case_id}: {missing}")

        row = output_by_case[case_id]
        expected = row.setdefault("expected", {})
        expected["relevant_evidence_ids"] = evidence_ids
        original_citation = copy.deepcopy((expected.get("required_citations") or [{}])[0])
        first = by_evidence[evidence_ids[0]]
        original_citation["page"] = int(first.get("page") or 0)
        original_citation["section"] = " / ".join(first.get("parent_path") or [])
        expected["required_citations"] = [original_citation]
        row["review_sidecar"] = {
            "status": str(manifest["dataset_tier"]),
            "reviewer_role": str(manifest["reviewer_role"]),
            "human_reviewer": "",
            "reviewed_at": "",
            "reason": str(repair.get("reason", "")),
        }

    for normalization in manifest.get("answer_normalizations", []):
        case_id = str(normalization.get("case_id", ""))
        if case_id not in output_by_case:
            raise ValueError(f"normalization case does not exist: {case_id}")
        row = output_by_case[case_id]
        if "reference_answer" in normalization:
            row["reference_answer"] = str(normalization["reference_answer"])
        if "answer_facts" in normalization:
            facts = normalization["answer_facts"]
            if not isinstance(facts, list):
                raise ValueError(f"answer_facts must be a list for {case_id}")
            row.setdefault("expected", {})["answer_facts"] = copy.deepcopy(facts)
        if "calculation" in normalization:
            row.setdefault("expected", {})["calculation"] = copy.deepcopy(normalization["calculation"])
        row.setdefault("normalization_sidecar", {})["status"] = "normalized_pending_independent_human_review"

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a non-Gold CFQA visual-repair manifest")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = apply_repairs(
        load_jsonl(args.cases),
        load_jsonl(args.chunks),
        json.loads(args.manifest.read_text(encoding="utf-8")),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(rows), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
