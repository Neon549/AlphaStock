"""Deterministic retrieval-only evaluation for Agent Memory Index."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CASES = ROOT / "evaluation" / "datasets" / "memory_index_eval.jsonl"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def evaluate_cases(cases: list[dict[str, Any]], searcher: Callable[..., list[dict[str, Any]]], *, k: int = 3) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    for case in cases:
        expected = set(case["relevant_source_paths"])
        forbidden = set(case.get("forbidden_source_paths", []))
        results = searcher(case["query"], top_k=k)
        paths = [item["source_path"] for item in results]
        first_rank = next((index + 1 for index, path in enumerate(paths) if path in expected), None)
        relevant_hits = sum(path in expected for path in paths)
        invalid_evidence_class = [
            item.get("source_path") for item in results
            if item.get("metadata", {}).get("evidence_class", "operating_knowledge") != "operating_knowledge"
        ]
        details.append({
            "id": case["id"], "scope": case.get("scope"), "hit": first_rank is not None,
            "rank": first_rank, "paths": paths,
            "precision": relevant_hits / len(paths) if paths else 0.0,
            "forbidden_hit": any(path in forbidden for path in paths),
            "invalid_evidence_class": invalid_evidence_class,
        })
    total = len(details)
    hits = sum(item["hit"] for item in details)
    reciprocal_rank = sum(1 / item["rank"] for item in details if item["rank"])
    forbidden_hits = sum(item["forbidden_hit"] for item in details)
    invalid_classes = sum(bool(item["invalid_evidence_class"]) for item in details)
    return {
        "cases": total,
        f"recall_at_{k}": round(hits / total, 4) if total else 0.0,
        "mrr": round(reciprocal_rank / total, 4) if total else 0.0,
        f"precision_at_{k}": round(sum(item["precision"] for item in details) / total, 4) if total else 0.0,
        "forbidden_recall_rate": round(forbidden_hits / total, 4) if total else 0.0,
        "evidence_class_violation_rate": round(invalid_classes / total, 4) if total else 0.0,
        "misses": [item["id"] for item in details if not item["hit"]],
        "details": details,
    }


def main() -> int:
    from agent_runtime.memory.index import search_memory
    print(json.dumps(evaluate_cases(load_cases(), search_memory), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
