"""Evaluate final intent, slots, and multi-intent task graphs on frozen JSONL.

This evaluates the actual routing contract (rules + fastText + LLM fallback),
not merely the raw fastText label.  Keep the dataset independent from the
training corpus; do not add evaluation examples back into ``train.txt``.

Usage:
    python scripts/evaluate_intent_routing.py
    python scripts/evaluate_intent_routing.py --dataset data/intent/eval_smoke.jsonl --out runtime/reports/intent_eval.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "intent" / "eval_smoke.jsonl"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.intent_parser import parse_intent
from agent_runtime.planning.task_graph import TaskGraphError, build_task_dag


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(row, dict) or not isinstance(row.get("query"), str) or not isinstance(row.get("expected"), dict):
            raise ValueError(f"line {line_number} must contain query and expected objects")
        rows.append(row)
    if not rows:
        raise ValueError("evaluation dataset is empty")
    return rows


def _actual_task_signature(tasks: list[dict[str, Any]]) -> Counter[tuple[Any, ...]]:
    task_intents = {task["task_id"]: task["intent"] for task in tasks}
    return Counter(
        (
            task["intent"],
            tuple(sorted(task_intents[dependency] for dependency in task.get("depends_on", []))),
            bool(task.get("requires_confirmation")),
            task.get("slots", {}).get("stock_code"),
            task.get("slots", {}).get("analyst_focus"),
        )
        for task in tasks
    )


def _expected_task_signature(tasks: list[dict[str, Any]]) -> Counter[tuple[Any, ...]]:
    return Counter(
        (
            task["intent"],
            tuple(sorted(task.get("depends_on_intents", []))),
            bool(task.get("requires_confirmation", False)),
            task.get("stock_code"),
            task.get("analyst_focus"),
        )
        for task in tasks
    )


def evaluate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks = Counter()
    denominators = Counter()
    failures: list[dict[str, Any]] = []
    confusion: Counter[tuple[int, int]] = Counter()
    source_counts: Counter[str] = Counter()
    multi_intent_count = 0

    for row in rows:
        expected = row["expected"]
        parsed = parse_intent(row["query"])
        expected_intent = int(expected["intent"])
        actual_intent = int(parsed.get("intent", 4))
        confusion[(expected_intent, actual_intent)] += 1
        source_counts[str(parsed.get("source") or "unknown")] += 1
        multi_intent_count += int(bool(parsed.get("multi_intent")))
        try:
            plan = build_task_dag(parsed.get("sub_intents"))
        except TaskGraphError as exc:
            plan = {"tasks": [], "error": str(exc)}

        outcome: dict[str, bool] = {
            "intent_exact": parsed.get("intent") == expected.get("intent"),
            "task_graph_exact": _actual_task_signature(plan.get("tasks", [])) == _expected_task_signature(expected.get("tasks", [])),
        }
        if "stock_code" in expected:
            outcome["stock_code_exact"] = parsed.get("stock_code") == expected["stock_code"]
        if "analyst_focus" in expected:
            outcome["analyst_focus_exact"] = parsed.get("analyst_focus") == expected["analyst_focus"]

        for name, passed in outcome.items():
            denominators[name] += 1
            checks[name] += int(passed)
        end_to_end = all(outcome.values())
        denominators["end_to_end_exact"] += 1
        checks["end_to_end_exact"] += int(end_to_end)
        if not end_to_end:
            failures.append({
                "id": row.get("id"),
                "query": row["query"],
                "expected": expected,
                "actual": {
                    "intent": parsed.get("intent"),
                    "stock_code": parsed.get("stock_code"),
                    "analyst_focus": parsed.get("analyst_focus"),
                    "source": parsed.get("source"),
                    "sub_intents": plan.get("tasks", []),
                },
                "failed_checks": [name for name, passed in outcome.items() if not passed],
            })

    metrics = {
        name: round(checks[name] / denominators[name], 4)
        for name in sorted(denominators)
    }
    labels = sorted({expected for expected, _ in confusion} | {actual for _, actual in confusion})
    per_intent = {}
    f1_values = []
    for label in labels:
        true_positive = confusion[(label, label)]
        false_positive = sum(confusion[(expected, label)] for expected in labels if expected != label)
        false_negative = sum(confusion[(label, actual)] for actual in labels if actual != label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_intent[str(label)] = {
            "support": sum(confusion[(label, actual)] for actual in labels),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    metrics["intent_macro_f1"] = round(sum(f1_values) / len(f1_values), 4) if f1_values else 0.0
    return {
        "dataset_size": len(rows),
        "metrics": metrics,
        "intent_per_class": per_intent,
        "intent_confusion_matrix": {
            str(expected): {str(actual): confusion[(expected, actual)] for actual in labels}
            for expected in labels
        },
        "routing_observability": {
            "source_counts": dict(source_counts),
            "llm_fallback_rate": round(
                sum(count for source, count in source_counts.items() if source.startswith("llm")) / len(rows),
                4,
            ),
            "multi_intent_rate": round(multi_intent_count / len(rows), 4),
        },
        "failures": failures,
        "notes": [
            "This is a seeded regression/smoke set, not a production-representative accuracy claim.",
            "Use a separate, frozen real-query set before reporting online routing accuracy.",
        ],
    }


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    argument_parser.add_argument("--out", type=Path, help="Optional JSON report path")
    args = argument_parser.parse_args()

    report = evaluate_rows(_read_jsonl(args.dataset))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
