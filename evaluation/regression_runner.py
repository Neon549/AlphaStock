"""Offline regression checks for deterministic AlphaStock controls.

This suite intentionally does not call an LLM, market-data API, or database.
It protects the rules that must never regress: scope validation and the
publication gate.  Keep these cases fast enough to run on every pull request.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.workflows.governance import evaluate_output_gate, validate_analysis_scope


DEFAULT_CASES = ROOT / "evaluation" / "datasets" / "workflow_regression.jsonl"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}") from exc
    return cases


def _assert_expected(case_id: str, actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key, value in expected.items():
        if key == "violations_include":
            violations = " ".join(actual.get("violations", []))
            for fragment in value:
                if fragment not in violations:
                    errors.append(f"{case_id}: missing violation fragment {fragment!r}")
            continue
        if key == "reasons_include":
            reasons = " ".join(actual.get("publish_reasons", []))
            for fragment in value:
                if fragment not in reasons:
                    errors.append(f"{case_id}: missing publish reason fragment {fragment!r}")
            continue
        if actual.get(key) != value:
            errors.append(
                f"{case_id}: expected {key}={value!r}, got {actual.get(key)!r}"
            )
    return errors


def run_case(case: dict[str, Any]) -> list[str]:
    case_id = case["id"]
    kind = case["kind"]
    payload = case["input"]

    if kind == "scope":
        result = validate_analysis_scope(
            payload.get("stock_code", ""),
            payload.get("analyst_focus", "all"),
            "x" * int(payload.get("doc_context_length", 0)),
        )
    elif kind == "publication_gate":
        result = evaluate_output_gate(payload)
    else:
        return [f"{case_id}: unknown regression case kind {kind!r}"]

    return _assert_expected(case_id, result, case["expected"])


def run(path: Path = DEFAULT_CASES) -> tuple[int, list[str]]:
    failures: list[str] = []
    cases = load_cases(path)
    for case in cases:
        failures.extend(run_case(case))
    return len(cases), failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline workflow regression cases")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()

    total, failures = run(args.cases)
    if failures:
        print(f"FAILED: {len(failures)} assertion(s) across {total} case(s)")
        print("\n".join(failures))
        return 1
    print(f"PASSED: {total}/{total} workflow regression cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
