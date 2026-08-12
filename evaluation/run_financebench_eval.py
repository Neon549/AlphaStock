"""Evaluate AlphaStock retrieval on the public human-annotated FinanceBench sample."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.rag_golden_eval import evaluate_retrieval_cases, load_cases
from evaluation.run_candidate_rag_eval import EMBEDDING_BACKENDS, _scoped_corpus, build_scoped_bm25_retriever, load_candidate_corpus, load_local_embedding_backend
from evaluation.rag_snapshot_retrievers import build_bm25_retriever, build_dense_retriever_from_vectors, build_hybrid_rrf_retriever, build_reranked_retriever


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "financebench_v1" / "rag_public_gold.jsonl"
DEFAULT_CHUNKS = ROOT / "runtime" / "reports" / "financebench-v1.pages.jsonl"
DEFAULT_SOURCE_MANIFEST = ROOT / "evaluation" / "corpus" / "financebench_v1" / "sources.json"
RERANKER_BACKENDS = {"bge_reranker_v2_m3": "BAAI/bge-reranker-v2-m3"}


def _aggregate_details(details: list[dict[str, Any]], cases: list[dict[str, Any]], *, k: int) -> dict[str, Any]:
    answerable = [item for item in details if item["abstain_retrieval_ok"] is None]
    abstentions = [item for item in details if item["abstain_retrieval_ok"] is not None]
    total = len(answerable)
    mean = lambda values: sum(values) / len(values) if values else 0.0
    report = {
        "cases": len(details),
        f"hit_rate_at_{k}": round(mean([float(item["hit"]) for item in answerable]), 4),
        f"recall_at_{k}": round(mean([float(item["recall"]) for item in answerable]), 4),
        f"precision_at_{k}": round(mean([float(item["precision"]) for item in answerable]), 4),
        f"f1_at_{k}": round(mean([
            2 * item["precision"] * item["recall"] / (item["precision"] + item["recall"])
            if item["precision"] + item["recall"] else 0.0
            for item in answerable
        ]), 4),
        "mrr": round(mean([1 / item["rank"] for item in answerable if item["rank"]]), 4),
        f"ndcg_at_{k}": round(mean([float(item["ndcg"]) for item in answerable]), 4),
        "citation_hit_rate": round(mean([float(item["citation_hit"]) for item in answerable]), 4),
        "abstain_retrieval_compliance_rate": round(mean([float(bool(item["abstain_retrieval_ok"])) for item in abstentions]), 4) if abstentions else None,
        "misses": [item["id"] for item in answerable if not item["hit"]],
        "details": details,
        "evaluation_note": "Gold document scope was supplied by the public benchmark metadata; this is a page-retrieval diagnostic, not end-to-end document discovery.",
    }
    return report


def _evaluate_gold_document_scope(
    cases: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    *,
    k: int,
    query_embedding=None,
    document_vectors: list[list[float]] | None = None,
) -> dict[str, dict[str, Any]]:
    by_document: dict[str, list[dict[str, Any]]] = {}
    for item in corpus:
        by_document.setdefault(str(item["document_id"]), []).append(item)
    methods: dict[str, list[dict[str, Any]]] = {"bm25_gold_document_scoped": []}
    vectors_by_evidence_id = {
        str(item["evidence_id"]): vector
        for item, vector in zip(corpus, document_vectors or [])
    }
    if document_vectors:
        methods["dense_bge_m3_gold_document_scoped"] = []
        methods["hybrid_rrf_bge_m3_gold_document_scoped"] = []
    cases_by_document: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        document_id = str(case.get("gold_annotation", {}).get("doc_name", ""))
        cases_by_document.setdefault(document_id, []).append(case)
    for document_id, document_cases in cases_by_document.items():
        scoped = by_document.get(document_id, [])
        if not scoped:
            raise ValueError(f"FinanceBench cases have no corpus document {document_id}")
        bm25 = build_bm25_retriever(scoped)
        methods["bm25_gold_document_scoped"].extend(evaluate_retrieval_cases(document_cases, bm25, k=k)["details"])
        if document_vectors:
            scoped_vectors = [vectors_by_evidence_id[str(item["evidence_id"])] for item in scoped]
            dense = build_dense_retriever_from_vectors(scoped, scoped_vectors, query_embedding)
            hybrid = build_hybrid_rrf_retriever(scoped, bm25, dense)
            methods["dense_bge_m3_gold_document_scoped"].extend(evaluate_retrieval_cases(document_cases, dense, k=k)["details"])
            methods["hybrid_rrf_bge_m3_gold_document_scoped"].extend(evaluate_retrieval_cases(document_cases, hybrid, k=k)["details"])
    return {name: _aggregate_details(details, cases, k=k) for name, details in methods.items()}


def _build_scoped_dense_hybrid_retrievers(
    corpus: list[dict[str, Any]],
    document_vectors: list[list[float]],
    query_embedding,
) -> dict[str, Any]:
    """Reuse snapshot vectors while applying the same deterministic scope as BM25."""

    vector_by_id = {str(item["evidence_id"]): vector for item, vector in zip(corpus, document_vectors)}
    cache: dict[tuple[str, ...], dict[str, Any]] = {}

    def retrieve(method: str, query: str, *, top_k: int) -> list[dict[str, Any]]:
        scoped = _scoped_corpus(query, corpus)
        if not scoped:
            return []
        key = tuple(str(item["evidence_id"]) for item in scoped)
        if key not in cache:
            bm25 = build_bm25_retriever(scoped)
            dense = build_dense_retriever_from_vectors(
                scoped,
                [vector_by_id[str(item["evidence_id"])] for item in scoped],
                query_embedding,
            )
            cache[key] = {"dense": dense, "hybrid_rrf": build_hybrid_rrf_retriever(scoped, bm25, dense)}
        return cache[key][method](query, top_k=top_k)

    return {
        "dense_entity_period_scoped": lambda query, *, top_k: retrieve("dense", query, top_k=top_k),
        "hybrid_rrf_entity_period_scoped": lambda query, *, top_k: retrieve("hybrid_rrf", query, top_k=top_k),
    }


def _load_reranker(name: str):
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(RERANKER_BACKENDS[name])

    def rerank(query: str, passages: list[str]) -> list[float]:
        return [float(value) for value in model.predict([(query, passage) for passage in passages], show_progress_bar=False)]

    return rerank


def run(
    cases_path: Path = DEFAULT_CASES,
    chunks_path: Path = DEFAULT_CHUNKS,
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
    *,
    k: int = 10,
    embedding_model: str | None = None,
    reranker_model: str | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    corpus = load_candidate_corpus(chunks_path, source_manifest)
    retrievers = {
        "bm25_global": build_bm25_retriever(corpus),
        "bm25_entity_period_scoped": build_scoped_bm25_retriever(corpus),
    }
    document_embedding = query_embedding = embedding_runtime = None
    document_vectors = None
    if embedding_model:
        document_embedding, query_embedding, embedding_runtime = load_local_embedding_backend(embedding_model)
        # Gold-document scope changes candidates, not source text. Encode each
        # page and query once, then reuse the vectors for every document scope.
        document_vectors = document_embedding([str(item["content"]) for item in corpus])
        query_values = [str(case["query"]) for case in cases]
        query_vector_by_text = dict(zip(query_values, query_embedding(query_values)))

        def cached_query_embedding(texts: list[str]) -> list[list[float]]:
            return [query_vector_by_text[text] for text in texts]

        query_embedding = cached_query_embedding
        dense_global = build_dense_retriever_from_vectors(corpus, document_vectors, query_embedding)
        hybrid_global = build_hybrid_rrf_retriever(corpus, retrievers["bm25_global"], dense_global)
        retrievers.update({
            "dense_global": dense_global,
            "hybrid_rrf_global": hybrid_global,
            **_build_scoped_dense_hybrid_retrievers(corpus, document_vectors, query_embedding),
        })
        if reranker_model:
            reranker = _load_reranker(reranker_model)
            retrievers["hybrid_rrf_global_reranked"] = build_reranked_retriever(hybrid_global, reranker)
            retrievers["hybrid_rrf_entity_period_scoped_reranked"] = build_reranked_retriever(
                retrievers["hybrid_rrf_entity_period_scoped"], reranker
            )
    gold_document_results = _evaluate_gold_document_scope(
        cases,
        corpus,
        k=k,
        query_embedding=query_embedding,
        document_vectors=document_vectors,
    )
    return {
        "dataset_id": "financebench-open-source-v1",
        "dataset_tier": "external_gold",
        "dataset_claim": "Public human-annotated financial QA benchmark; not AlphaStock online traffic or production-representative quality.",
        "cases": len(cases),
        "corpus_chunks": len(corpus),
        "k": k,
        "embedding_runtime": embedding_runtime,
        "reranker": RERANKER_BACKENDS.get(reranker_model),
        "results": {
            **{
                name: evaluate_retrieval_cases(cases, retriever, k=k)
                for name, retriever in retrievers.items()
            },
            **gold_document_results,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AlphaStock retrieval on public FinanceBench Gold")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--embedding-model", choices=tuple(EMBEDDING_BACKENDS), help="Optional Dense/RRF model for global, scoped and Gold-document diagnostics.")
    parser.add_argument("--reranker-model", choices=tuple(RERANKER_BACKENDS), help="Optional cross-encoder reranker applied to Hybrid's top-100 candidates.")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.cases, args.chunks, args.source_manifest, k=args.k, embedding_model=args.embedding_model, reranker_model=args.reranker_model)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "dataset_id": report["dataset_id"],
        "cases": report["cases"],
        "results": {
            method: {key: value for key, value in metrics.items() if key in {"recall_at_10", "mrr", "ndcg_at_10", "citation_hit_rate"}}
            for method, metrics in report["results"].items()
        },
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
