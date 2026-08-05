"""Run reproducible BM25/dense/Hybrid-RRF Golden Set retrieval baselines."""

from __future__ import annotations

import json
import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.rag_golden_eval import evaluate_retrieval_cases, load_cases
from evaluation.rag_snapshot_retrievers import (
    build_bm25_retriever,
    build_dense_retriever,
    build_hybrid_rrf_retriever,
    load_snapshot,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run fixed-corpus RAG retrieval baselines")
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=("bm25_only", "pgvector_dense", "hybrid_rrf"),
        default=["bm25_only"],
        help="Dense/Hybrid require the project embedding model to be preloaded locally; default is offline BM25 only.",
    )
    args = parser.parse_args(argv)
    corpus = load_snapshot()
    bm25 = build_bm25_retriever(corpus)
    retrievers = {"bm25_only": bm25}
    if any(name in args.methods for name in ("pgvector_dense", "hybrid_rrf")):
        # A Golden Set runner must not silently download model weights or make
        # evaluation quality depend on network availability.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from rag.news_indexer import _embed

        dense = build_dense_retriever(corpus, _embed)
        retrievers["pgvector_dense"] = dense
        retrievers["hybrid_rrf"] = build_hybrid_rrf_retriever(corpus, bm25, dense)
    selected = {name: retrievers[name] for name in args.methods}
    results = {name: evaluate_retrieval_cases(load_cases(), retriever, k=3) for name, retriever in selected.items()}
    print(json.dumps({"corpus_version": "sha256:fixture-rag-corpus-v1", "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
