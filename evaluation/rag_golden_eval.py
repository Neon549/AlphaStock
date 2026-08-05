"""Deterministic evaluation for a versioned RAG Golden Set.

This module does not call an LLM. It measures retriever quality and the
mechanically checkable part of answer governance against an immutable corpus
snapshot. Faithfulness can be added as a separately versioned model-judge job.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "datasets" / "rag_golden_seed.jsonl"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def _citation_key(item: dict[str, Any]) -> tuple[str, int, str]:
    return (str(item.get("filename", "")), int(item.get("page") or 0), str(item.get("section", "")))


def evaluate_retrieval_cases(
    cases: list[dict[str, Any]], retriever: Callable[..., list[dict[str, Any]]], *, k: int = 5
) -> dict[str, Any]:
    """Evaluate only against an immutable corpus version and evidence IDs."""

    details: list[dict[str, Any]] = []
    for case in cases:
        expected = case["expected"]
        relevant = set(expected["relevant_evidence_ids"])
        abstain_allowed = bool(expected.get("abstain_allowed", False))
        required_citations = {_citation_key(item) for item in expected.get("required_citations", [])}
        results = retriever(case["query"], top_k=k)
        ids = [item.get("evidence_id") for item in results]
        rank = next((index + 1 for index, value in enumerate(ids) if value in relevant), None)
        abstain_retrieval_ok = not ids if not relevant and abstain_allowed else None
        gains = [1 if value in relevant else 0 for value in ids]
        dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
        ideal = sum(1 / math.log2(index + 2) for index in range(min(len(relevant), k)))
        retrieved_citations = {_citation_key(item) for item in results}
        citation_hit = bool(required_citations & retrieved_citations) if required_citations else rank is not None
        details.append({
            "id": case["id"], "corpus_version": case["corpus_version"], "rank": rank,
            "hit": rank is not None, "abstain_retrieval_ok": abstain_retrieval_ok, "ndcg": dcg / ideal if ideal else 0.0,
            "citation_hit": citation_hit, "result_ids": ids,
        })

    answerable = [item for item in details if item["abstain_retrieval_ok"] is None]
    abstention_cases = [item for item in details if item["abstain_retrieval_ok"] is not None]
    total = len(answerable)
    return {
        "cases": len(details),
        f"recall_at_{k}": round(sum(item["hit"] for item in details) / total, 4) if total else 0.0,
        "mrr": round(sum(1 / item["rank"] for item in answerable if item["rank"]) / total, 4) if total else 0.0,
        f"ndcg_at_{k}": round(sum(item["ndcg"] for item in answerable) / total, 4) if total else 0.0,
        "citation_hit_rate": round(sum(item["citation_hit"] for item in answerable) / total, 4) if total else 0.0,
        "abstain_retrieval_compliance_rate": round(sum(bool(item["abstain_retrieval_ok"]) for item in abstention_cases) / len(abstention_cases), 4) if abstention_cases else None,
        "misses": [item["id"] for item in answerable if not item["hit"]],
        "details": details,
    }


def evaluate_answer_governance(cases: list[dict[str, Any]], answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check citation backlink and abstention rules without judging prose quality."""

    details: list[dict[str, Any]] = []
    for case in cases:
        expected = case["expected"]
        answer = answers.get(case["id"], {})
        required = {_citation_key(item) for item in expected.get("required_citations", [])}
        actual = {_citation_key(item) for item in answer.get("citations", [])}
        abstained = bool(answer.get("abstained"))
        citation_ok = required.issubset(actual) if required and not abstained else (abstained and expected.get("abstain_allowed", False))
        abstain_ok = (not abstained) or bool(expected.get("abstain_allowed", False))
        unsupported = bool(answer.get("unsupported_claims", []))
        details.append({"id": case["id"], "citation_ok": citation_ok, "abstain_ok": abstain_ok, "unsupported": unsupported})
    total = len(details)
    return {
        "cases": total,
        "citation_backlink_rate": round(sum(item["citation_ok"] for item in details) / total, 4) if total else 0.0,
        "abstain_compliance_rate": round(sum(item["abstain_ok"] for item in details) / total, 4) if total else 0.0,
        "unsupported_answer_rate": round(sum(item["unsupported"] for item in details) / total, 4) if total else 0.0,
        "details": details,
    }
