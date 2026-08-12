"""Run a retrieval-only baseline against the untouched held-out filing corpus.

The runner intentionally reuses the same deterministic evaluators as the
candidate baseline, but makes the corpus manifest explicit.  The resulting
numbers remain candidate metrics until the query provenance and Gold labels
have independent human review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.run_candidate_rag_eval import EMBEDDING_BACKENDS, run


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "heldout_public_filings_v1" / "rag_manual_expert_candidates.jsonl"
DEFAULT_CHUNKS = ROOT / "runtime" / "reports" / "heldout-public-filings-v1.chunks.jsonl"
DEFAULT_SOURCE_MANIFEST = ROOT / "evaluation" / "corpus" / "heldout_public_filings_v1" / "sources.json"


def _metric_summary(report: dict) -> dict:
    """Keep the @K curve compact while the headline report retains traces."""

    keys = (
        "hit_rate_at_{k}",
        "recall_at_{k}",
        "precision_at_{k}",
        "f1_at_{k}",
        "mrr",
        "ndcg_at_{k}",
        "citation_hit_rate",
        "abstain_retrieval_compliance_rate",
    )
    return {
        method: {
            key.format(k=report["k"]): result.get(key.format(k=report["k"]))
            if "{k}" in key
            else result.get(key)
            for key in keys
        }
        for method, result in report["results"].items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the held-out public-filing RAG candidate set")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--ks",
        type=int,
        nargs="+",
        default=[1, 3, 5, 10],
        help="Additional cutoffs for a compact retrieval curve; must be positive.",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=("bm25_global", "bm25_entity_period_scoped", "bm25_entity_period_scoped_alias", "dense_entity_period_scoped", "hybrid_rrf_entity_period_scoped"),
        default=["bm25_global", "bm25_entity_period_scoped", "bm25_entity_period_scoped_alias"],
    )
    parser.add_argument("--embedding-model", choices=tuple(EMBEDDING_BACKENDS), default="project_text2vec")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.k <= 0 or any(k <= 0 for k in args.ks):
        raise ValueError("all K values must be positive")
    shared = {
        "source_manifest": args.source_manifest,
        "methods": tuple(args.methods),
        "embedding_model": args.embedding_model,
        "dataset_tier": "heldout_manual_expert_candidate_pending_human_review",
        "claim_boundary": (
            "Held-out documents are isolated from retriever selection, but queries are manually authored and labels await independent review; "
            "this is not a production or resume metric."
        ),
    }
    report = run(
        args.cases,
        args.chunks,
        k=args.k,
        **shared,
    )
    report["k"] = args.k
    report["retrieval_curve"] = {}
    for k in sorted(set(args.ks)):
        curve_report = report if k == args.k else run(args.cases, args.chunks, k=k, **shared)
        curve_report["k"] = k
        report["retrieval_curve"][f"at_{k}"] = _metric_summary(curve_report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
