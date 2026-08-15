"""Compare baseline and deterministic query rewrite on a pinned candidate set.

This runner intentionally reports retrieval diagnostics only.  It never calls
an LLM, never changes labels, and refuses to describe synthetic/pending cases
as independently reviewed real-user evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.download_corpus import DEFAULT_SOURCE_MANIFEST
from evaluation.rag_golden_eval import evaluate_retrieval_cases, load_cases
from evaluation.run_candidate_rag_eval import DEFAULT_CHUNKS, build_scoped_bm25_retriever, load_candidate_corpus
from rag.query_rewrite import rewrite_retrieval_query


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_query_variants.jsonl"


def _details_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in report.get("details", [])}


def _case_deltas(baseline: dict[str, Any], rewritten: dict[str, Any]) -> dict[str, Any]:
    before = _details_by_id(baseline)
    after = _details_by_id(rewritten)
    items: list[dict[str, Any]] = []
    win = loss = tie = 0
    for case_id in sorted(before):
        left, right = before[case_id], after[case_id]
        left_rank = left.get("rank") or 10_000
        right_rank = right.get("rank") or 10_000
        if bool(right.get("hit")) and not bool(left.get("hit")) or right_rank < left_rank:
            outcome = "rewrite_win"
            win += 1
        elif bool(left.get("hit")) and not bool(right.get("hit")) or left_rank < right_rank:
            outcome = "rewrite_loss"
            loss += 1
        else:
            outcome = "tie"
            tie += 1
        items.append({
            "id": case_id,
            "outcome": outcome,
            "baseline_rank": left.get("rank"),
            "rewritten_rank": right.get("rank"),
            "baseline_hit": bool(left.get("hit")),
            "rewritten_hit": bool(right.get("hit")),
            "baseline_result_ids": left.get("result_ids", []),
            "rewritten_result_ids": right.get("result_ids", []),
        })
    return {"wins": win, "losses": loss, "ties": tie, "items": items}


def run(
    cases_path: Path = DEFAULT_CASES,
    chunks_path: Path = DEFAULT_CHUNKS,
    *,
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
    k: int = 10,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    corpus = load_candidate_corpus(chunks_path, source_manifest)
    # This preserves the project's existing entity/period scope and legacy
    # filing aliases.  The treatment is strictly the new rewrite plan.
    baseline_retriever = build_scoped_bm25_retriever(corpus, expand_query=True)

    def rewritten_retriever(query: str, *, top_k: int) -> list[dict[str, Any]]:
        plan = rewrite_retrieval_query(query)
        return baseline_retriever(plan["rewritten_query"], top_k=top_k)

    baseline = evaluate_retrieval_cases(cases, baseline_retriever, k=k)
    rewritten = evaluate_retrieval_cases(cases, rewritten_retriever, k=k)
    metric_keys = (
        f"hit_rate_at_{k}", f"recall_at_{k}", f"precision_at_{k}",
        f"f1_at_{k}", "mrr", f"ndcg_at_{k}", "citation_hit_rate",
    )
    return {
        "schema_version": "query-rewrite-ab/v1",
        "dataset_tier": "synthetic_candidate_pending_independent_review",
        "claim_boundary": (
            "Pinned synthetic query variants and candidate labels only; this is not independently human-reviewed real-user traffic, a production metric, or a resume claim."
        ),
        "cases": len(cases),
        "top_k": k,
        "baseline": baseline,
        "deterministic_rewrite": rewritten,
        "metric_delta": {
            key: round(float(rewritten.get(key) or 0.0) - float(baseline.get(key) or 0.0), 4)
            for key in metric_keys
        },
        "case_delta": _case_deltas(baseline, rewritten),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare deterministic query rewrite against the existing scoped BM25 baseline")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.cases, args.chunks, source_manifest=args.source_manifest, k=args.k)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "cases": report["cases"], "metric_delta": report["metric_delta"],
        "case_delta": {key: report["case_delta"][key] for key in ("wins", "losses", "ties")},
        "out": str(args.out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
