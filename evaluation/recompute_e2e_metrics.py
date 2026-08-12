"""Recompute page-level citation metrics from a completed E2E report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _page_match(expected: dict, actual: dict) -> bool:
    return (
        str(expected.get("filename", "")) == str(actual.get("filename", ""))
        and int(expected.get("page") or 0) == int(actual.get("page") or 0)
    )


def recompute(report: dict, cases: list[dict]) -> dict:
    by_id = {str(case["id"]): case for case in cases}
    judged = []
    for detail in report.get("details", []):
        case = by_id[str(detail["id"])]
        required = case.get("expected", {}).get("required_citations", [])
        citations = detail.get("generated", {}).get("citations", [])
        citation_ok = all(any(_page_match(item, actual) for actual in citations) for item in required) if required else True
        detail["citation_ok"] = citation_ok
        detail["grounded_answer_correct"] = bool(
            detail.get("judge", {}).get("correct") is True
            and citation_ok
            and detail.get("cited_retrieved_evidence", False)
        )
        if detail.get("judge", {}).get("correct") is not None:
            judged.append(detail)
    total = len(judged)
    correct_count = sum(item.get("judge", {}).get("correct") is True for item in judged)
    report["citation_accuracy"] = round(sum(item["citation_ok"] for item in judged) / total, 4) if total else None
    report["grounded_answer_accuracy"] = round(sum(item["grounded_answer_correct"] for item in judged) / total, 4) if total else None
    report["citation_validation"] = "filename+page"
    report["correct_cases"] = correct_count
    report["incorrect_cases"] = sum(item.get("judge", {}).get("correct") is False for item in judged)
    report["unjudged_cases"] = len(report.get("details", [])) - total
    report["answer_accuracy_all_cases_lower_bound"] = round(correct_count / len(report.get("details", [])), 4) if report.get("details") else None
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line.strip()]
    updated = recompute(report, cases)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: updated.get(key) for key in ("cases", "judged_cases", "answer_accuracy", "citation_accuracy", "grounded_answer_accuracy")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
