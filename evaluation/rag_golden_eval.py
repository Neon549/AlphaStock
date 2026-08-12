"""Deterministic evaluation for a versioned RAG Golden Set.

This module does not call an LLM. It measures retriever quality and the
mechanically checkable part of answer governance against an immutable corpus
snapshot. Faithfulness can be added as a separately versioned model-judge job.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "datasets" / "rag_golden_seed.jsonl"


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def _citation_key(item: dict[str, Any]) -> tuple[str, int, str]:
    return (str(item.get("filename", "")), int(item.get("page") or 0), str(item.get("section", "")))


def citation_matches(required: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Match exact file/page while allowing a more specific indexed section."""

    required_filename, required_page, required_section = _citation_key(required)
    actual_filename, actual_page, actual_section = _citation_key(actual)
    if (required_filename, required_page) != (actual_filename, actual_page):
        return False
    if not required_section:
        return True
    return actual_section == required_section or actual_section.startswith(f"{required_section} / ")


def _citations_cover(required: list[dict[str, Any]], actual: list[dict[str, Any]], *, require_all: bool) -> bool:
    matches = [any(citation_matches(expected, observed) for observed in actual) for expected in required]
    return all(matches) if require_all else any(matches)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def bootstrap_mean_interval(values: list[float], *, samples: int = 2000, seed: int = 20260812) -> dict[str, float | int] | None:
    """Deterministic non-parametric 95% CI for a per-case metric.

    This reports finite-set uncertainty; it never turns a candidate dataset
    into representative production traffic.
    """

    if not values:
        return None
    rng = random.Random(seed)
    count = len(values)
    means = sorted(sum(values[rng.randrange(count)] for _ in range(count)) / count for _ in range(samples))
    return {
        "point_estimate": round(_mean(values), 4),
        "lower_95": round(_percentile(means, 0.025), 4),
        "upper_95": round(_percentile(means, 0.975), 4),
        "cases": count,
        "method": "nonparametric_bootstrap",
        "samples": samples,
        "seed": seed,
    }


def _retrieval_reporting(details: list[dict[str, Any]], cases: list[dict[str, Any]], *, k: int) -> dict[str, Any]:
    """Attach confidence intervals and source/tag slices to retrieval metrics."""

    by_case = {str(case["id"]): case for case in cases}
    answerable = [item for item in details if item["abstain_retrieval_ok"] is None]
    abstentions = [item for item in details if item["abstain_retrieval_ok"] is not None]

    def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "cases": len(items),
            f"hit_rate_at_{k}": round(_mean([float(item["hit"]) for item in items]), 4),
            f"recall_at_{k}": round(_mean([float(item["recall"]) for item in items]), 4),
            f"precision_at_{k}": round(_mean([float(item["precision"]) for item in items]), 4),
            "mrr": round(_mean([1 / item["rank"] if item["rank"] else 0.0 for item in items]), 4),
            f"ndcg_at_{k}": round(_mean([float(item["ndcg"]) for item in items]), 4),
            "citation_hit_rate": round(_mean([float(item["citation_hit"]) for item in items]), 4),
        }

    source_groups: dict[str, list[dict[str, Any]]] = {}
    tag_groups: dict[str, list[dict[str, Any]]] = {}
    for detail in answerable:
        case = by_case[str(detail["id"])]
        source_groups.setdefault(str(case.get("source_type") or "unclassified"), []).append(detail)
        for tag in case.get("tags", []):
            tag_groups.setdefault(str(tag), []).append(detail)
    return {
        "uncertainty": {
            f"hit_rate_at_{k}": bootstrap_mean_interval([float(item["hit"]) for item in answerable]),
            f"recall_at_{k}": bootstrap_mean_interval([float(item["recall"]) for item in answerable]),
            f"precision_at_{k}": bootstrap_mean_interval([float(item["precision"]) for item in answerable]),
            "mrr": bootstrap_mean_interval([1 / item["rank"] if item["rank"] else 0.0 for item in answerable]),
            f"ndcg_at_{k}": bootstrap_mean_interval([float(item["ndcg"]) for item in answerable]),
            "citation_hit_rate": bootstrap_mean_interval([float(item["citation_hit"]) for item in answerable]),
            "abstain_retrieval_compliance_rate": bootstrap_mean_interval([float(bool(item["abstain_retrieval_ok"])) for item in abstentions]),
            "warning": "Intervals quantify finite-set uncertainty only; provenance determines whether a result is reportable.",
        },
        "breakdown_by_source_type": {name: summary(items) for name, items in sorted(source_groups.items())},
        "breakdown_by_tag": {name: summary(items) for name, items in sorted(tag_groups.items())},
    }


def evaluate_retrieval_cases(
    cases: list[dict[str, Any]], retriever: Callable[..., list[dict[str, Any]]], *, k: int = 5
) -> dict[str, Any]:
    """Evaluate only against an immutable corpus version and evidence IDs."""

    details: list[dict[str, Any]] = []
    for case in cases:
        expected = case["expected"]
        relevant = set(expected["relevant_evidence_ids"])
        abstain_allowed = bool(expected.get("abstain_allowed", False))
        required_citations = expected.get("required_citations", [])
        results = retriever(case["query"], top_k=k)
        # A retriever can rank smaller chunks while the benchmark annotation
        # remains page-level. ``page_evidence_id`` provides that stable
        # backlink; existing corpora without it retain their evidence ID.
        ids = [str(item.get("page_evidence_id") or item.get("evidence_id")) for item in results]
        rank = next((index + 1 for index, value in enumerate(ids) if value in relevant), None)
        relevant_retrieved = len({value for value in ids if value in relevant})
        abstain_retrieval_ok = not ids if not relevant and abstain_allowed else None
        gains = [1 if value in relevant else 0 for value in ids]
        dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
        ideal = sum(1 / math.log2(index + 2) for index in range(min(len(relevant), k)))
        citation_hit = _citations_cover(required_citations, results, require_all=False) if required_citations else rank is not None
        details.append({
            "id": case["id"], "corpus_version": case["corpus_version"], "rank": rank,
            "hit": rank is not None, "abstain_retrieval_ok": abstain_retrieval_ok, "ndcg": dcg / ideal if ideal else 0.0,
            "citation_hit": citation_hit, "result_ids": ids, "relevant_retrieved": relevant_retrieved,
            # Fixed-K denominator prevents a retriever that returns only one
            # easy hit from receiving an inflated precision score.
            "precision": relevant_retrieved / k,
            "recall": relevant_retrieved / len(relevant) if relevant else 0.0,
        })

    answerable = [item for item in details if item["abstain_retrieval_ok"] is None]
    abstention_cases = [item for item in details if item["abstain_retrieval_ok"] is not None]
    total = len(answerable)
    report = {
        "cases": len(details),
        f"hit_rate_at_{k}": round(sum(item["hit"] for item in answerable) / total, 4) if total else 0.0,
        f"recall_at_{k}": round(sum(item["recall"] for item in answerable) / total, 4) if total else 0.0,
        f"precision_at_{k}": round(sum(item["precision"] for item in answerable) / total, 4) if total else 0.0,
        f"f1_at_{k}": round(
            _mean([
                2 * item["precision"] * item["recall"] / (item["precision"] + item["recall"])
                if item["precision"] + item["recall"] else 0.0
                for item in answerable
            ]), 4
        ) if total else 0.0,
        "mrr": round(sum(1 / item["rank"] for item in answerable if item["rank"]) / total, 4) if total else 0.0,
        f"ndcg_at_{k}": round(sum(item["ndcg"] for item in answerable) / total, 4) if total else 0.0,
        "citation_hit_rate": round(sum(item["citation_hit"] for item in answerable) / total, 4) if total else 0.0,
        "abstain_retrieval_compliance_rate": round(sum(bool(item["abstain_retrieval_ok"]) for item in abstention_cases) / len(abstention_cases), 4) if abstention_cases else None,
        "misses": [item["id"] for item in answerable if not item["hit"]],
        "details": details,
    }
    report.update(_retrieval_reporting(details, cases, k=k))
    return report


def evaluate_answer_governance(cases: list[dict[str, Any]], answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Check citation backlink and abstention rules without judging prose quality."""

    details: list[dict[str, Any]] = []
    for case in cases:
        expected = case["expected"]
        answer = answers.get(case["id"], {})
        required = expected.get("required_citations", [])
        actual = answer.get("citations", [])
        abstained = bool(answer.get("abstained"))
        citation_ok = _citations_cover(required, actual, require_all=True) if required and not abstained else (abstained and expected.get("abstain_allowed", False))
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
