"""Evaluate BM25/Dense/Hybrid/Rerank against the remote PostgreSQL news index.

The database is reached through a local SSH tunnel.  This runner intentionally
keeps the database read-only and writes only local runtime reports.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import psycopg2

from evaluation.evaluator import EVAL_DATASET, manual_context_recall
from evaluation.rag_snapshot_retrievers import (
    build_bm25_retriever,
    build_dense_retriever_from_vectors,
    build_hybrid_rrf_retriever,
    build_reranked_retriever,
)
from rag.news_indexer import news_evidence_snippet
from rag.retriever import expand_finance_query, finance_query_facets, finance_title_matches
from tools.stock_name_dict import get_stock_name


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "runtime" / "reports" / "remote-db-retrieval-ablation.json"
DEFAULT_EMBEDDING_MODEL = "bge_small_zh_v1_5_no_instruction"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

def expand_query(query: str) -> str:
    """Use the same deterministic finance expansion as production retrieval."""

    return expand_finance_query(query)


def _remote_connection(port: int):
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)
    info = psycopg2.extensions.parse_dsn(os.environ["POSTGRES_DSN"])
    return psycopg2.connect(
        host="127.0.0.1",
        port=port,
        dbname=info.get("dbname", ""),
        user=info.get("user", ""),
        password=info.get("password", ""),
        connect_timeout=8,
    )


def load_remote_corpus(
    port: int,
    *,
    source_kinds: set[str] | None = None,
    evidence_mode: str = "online",
) -> list[dict[str, Any]]:
    with _remote_connection(port) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT stock_code, title, stock_name, pub_time, date, full_text,
                   COALESCE(source_kind, 'news'), source_url, publisher
            FROM news_vectors
            ORDER BY date DESC, pub_time DESC
            """
        )
        raw_rows = cur.fetchall()
        # Official disclosures expose the current exchange-recognised short
        # name.  Preserve it as a trusted alias for news rows that may still
        # carry an older name in their stock_name metadata.
        trusted_aliases: dict[str, set[str]] = defaultdict(set)
        for raw_stock_code, _title, raw_stock_name, _pub_time, _date, _full_text, raw_source_kind, _source_url, _publisher in raw_rows:
            raw_stock_code = str(raw_stock_code)
            alias = str(raw_stock_name or "").strip()
            if (
                re.fullmatch(r"\d{6}", raw_stock_code)
                and str(raw_source_kind or "news") == "announcement"
                and alias
                and not re.fullmatch(r"\d{6}", alias)
            ):
                trusted_aliases[raw_stock_code].add(alias)

        rows = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for stock_code, title, stock_name, pub_time, date, full_text, source_kind, source_url, publisher in raw_rows:
            stock_code = str(stock_code)
            if not re.fullmatch(r"\d{6}", stock_code):
                continue
            source_kind = str(source_kind or "news")
            if source_kinds and source_kind not in source_kinds:
                continue
            title = str(title or "").strip()
            stock_name = str(stock_name or "").strip()
            # Mirror the production entity gate.  The upstream endpoint can
            # attach sector-wide listicles to a stock-code request; a stored
            # code is not enough unless the news headline identifies the
            # requested company or ticker.  Official announcements stay
            # available because their titles often omit the company name.
            canonical_name = get_stock_name(stock_code)
            entity_names = {
                *(trusted_aliases.get(stock_code, set())),
                *(() if canonical_name == "名称未验证" else (canonical_name,)),
            }
            if source_kind == "news" and stock_code not in title and not any(
                entity_name in title for entity_name in entity_names if entity_name
            ):
                continue
            key = (stock_code, title, str(date), source_kind, str(full_text or ""))
            if not title or key in seen:
                continue
            seen.add(key)
            evidence_id = f"{source_kind}:" + hashlib.sha256(
                f"{stock_code}|{date}|{pub_time}|{title}|{full_text}".encode("utf-8")
            ).hexdigest()[:24]
            title_text = f"[{pub_time}] {stock_name}({stock_code}) {title}"
            if evidence_mode == "title":
                text = title_text
            elif evidence_mode == "full":
                text = str(full_text or title_text)
            else:
                # Mirrors the online path: news headlines protect precision,
                # while official disclosure chunks expose primary-source facts.
                text = str(full_text or title_text) if source_kind == "announcement" else title_text
            rows.append(
                {
                    "evidence_id": evidence_id,
                    "content": text,
                    "text": text,
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "title": title,
                    "pub_time": str(pub_time or ""),
                    "date": str(date or ""),
                    "source_kind": source_kind,
                    "source_url": str(source_url or ""),
                    "publisher": str(publisher or ""),
                }
            )
    return rows


def _dedupe_source_documents(ranked: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """Keep the best query-ranked chunk for each source document."""

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        identity = _item_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        output.append(item)
        if len(output) >= top_k:
            break
    return output


def _item_identity(item: dict[str, Any]) -> str:
    source_url = str(item.get("source_url") or "")
    if source_url:
        return f"source:{source_url}"
    return f"{item.get('source_kind', 'news')}:title:{item.get('title', '')}"


def _subset_bm25(
    corpus: list[dict[str, Any]],
    stock_code: str,
    query: str,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    scoped = [item for item in corpus if item["stock_code"] == str(stock_code)]
    ranked = build_bm25_retriever(scoped)(expand_query(query), top_k=len(scoped))
    return _dedupe_source_documents(ranked, top_k)


def _faceted_bm25(
    corpus: list[dict[str, Any]],
    stock_code: str,
    query: str,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    scoped = [item for item in corpus if item["stock_code"] == str(stock_code)]
    retriever = build_bm25_retriever(scoped)
    primary = _dedupe_source_documents(
        retriever(expand_query(query), top_k=len(scoped)),
        len(scoped),
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    facets = finance_query_facets(query)
    for facet in facets:
        ranked = _dedupe_source_documents(retriever(facet, top_k=len(scoped)), len(scoped))
        for item in ranked:
            identity = _item_identity(item)
            if identity not in seen and finance_title_matches(str(item.get("title", "")), facet):
                output.append(item)
                seen.add(identity)
                break
        if len(output) >= top_k:
            return output
    for item in primary:
        identity = _item_identity(item)
        if identity not in seen:
            output.append(item)
            seen.add(identity)
        if len(output) >= top_k:
            break
    return output


def load_embedding_backend(name: str):
    from evaluation.run_candidate_rag_eval import load_local_embedding_backend

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    return load_local_embedding_backend(name)


def load_reranker(model_name: str):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    from sentence_transformers import CrossEncoder

    device = os.getenv("NEWS_RERANK_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
    batch_size = max(1, int(os.getenv("NEWS_RERANK_BATCH_SIZE", "16")))
    model = CrossEncoder(model_name, device=device, local_files_only=True)

    def rerank(query: str, passages: list[str]) -> list[float]:
        pairs = [(query, passage) for passage in passages]
        scores = model.predict(pairs, batch_size=batch_size, show_progress_bar=False)
        return [float(score) for score in scores]

    return rerank, {"model": model_name, "device": device, "batch_size": batch_size, "local_files_only": True}


def _validated_rerank_scores(
    scores: list[float],
    candidates: list[dict[str, Any]],
) -> list[float]:
    """Reject malformed Cross-Encoder output instead of silently truncating it."""

    if len(scores) != len(candidates):
        raise ValueError(
            f"reranker returned {len(scores)} scores for {len(candidates)} candidates"
        )
    values = [float(score) for score in scores]
    if not all(math.isfinite(score) for score in values):
        raise ValueError("reranker returned a non-finite score")
    return values


def _subset_retriever(
    corpus: list[dict[str, Any]],
    vectors: list[list[float]],
    query_embedding: Callable[[list[str]], list[list[float]]],
    stock_code: str,
    query: str,
    method: str,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    scoped = [item for item in corpus if item["stock_code"] == str(stock_code)]
    by_id = {item["evidence_id"]: vector for item, vector in zip(corpus, vectors)}
    scoped_vectors = [by_id[item["evidence_id"]] for item in scoped]
    bm25 = build_bm25_retriever(scoped)
    dense = build_dense_retriever_from_vectors(scoped, scoped_vectors, query_embedding)
    hybrid = build_hybrid_rrf_retriever(scoped, bm25, dense)
    query_for_bm25 = expand_query(query)
    if method == "bm25_scoped_expanded":
        return bm25(query_for_bm25, top_k=top_k)
    if method == "dense_scoped":
        return dense(query, top_k=top_k)
    return hybrid(query, top_k=top_k)


def _subset_reranked_retriever(
    corpus: list[dict[str, Any]],
    vectors: list[list[float]],
    query_embedding: Callable[[list[str]], list[list[float]]],
    reranker: Callable[[str, list[str]], list[float]],
    stock_code: str,
    query: str,
    *,
    top_k: int,
    candidate_k: int,
    preserve_k: int = 0,
) -> list[dict[str, Any]]:
    scoped = [item for item in corpus if item["stock_code"] == str(stock_code)]
    by_id = {item["evidence_id"]: vector for item, vector in zip(corpus, vectors)}
    scoped_vectors = [by_id[item["evidence_id"]] for item in scoped]
    bm25 = build_bm25_retriever(scoped)
    dense = build_dense_retriever_from_vectors(scoped, scoped_vectors, query_embedding)
    hybrid = build_hybrid_rrf_retriever(scoped, bm25, dense)
    candidates = hybrid(query, top_k=max(top_k, candidate_k))
    if not candidates:
        return []
    scores = _validated_rerank_scores(
        reranker(query, [str(item["content"]) for item in candidates]),
        candidates,
    )
    reranked = [
        {**item, "rerank_score": float(score)}
        for item, score in sorted(
            zip(candidates, scores),
            key=lambda pair: (-float(pair[1]), str(pair[0]["evidence_id"])),
        )
    ]
    if preserve_k <= 0:
        return reranked[:top_k]
    preserved = candidates[:preserve_k]
    preserved_ids = {item["evidence_id"] for item in preserved}
    output: list[dict[str, Any]] = []
    for item in preserved:
        matching = next((row for row in reranked if row["evidence_id"] == item["evidence_id"]), item)
        output.append(matching)
    for item in reranked:
        if item["evidence_id"] not in preserved_ids:
            output.append(item)
        if len(output) >= top_k:
            break
    return output[:top_k]


def _subset_bge_news_reranked(
    corpus: list[dict[str, Any]],
    reranker: Callable[[str, list[str]], list[float]],
    stock_code: str,
    query: str,
    *,
    top_k: int,
    candidate_k: int,
    bge_weight: float = 1.0,
) -> list[dict[str, Any]]:
    """Mirror production news retrieval: scoped BM25 then BGE reranking."""

    # A candidate pool wider than ``top_k`` is an explicit retrieval-policy
    # experiment.  Honour it here: the previous evaluator accepted candidate_k
    # but silently scored only Top-K, which made a labelled Top-20 comparison
    # indistinguishable from set-preserving Top-5 reranking.
    candidates = _faceted_bm25(corpus, stock_code, query, top_k=max(top_k, candidate_k))
    if not candidates:
        return []
    scores = _validated_rerank_scores(
        reranker(query, [str(item["content"]) for item in candidates]),
        candidates,
    )
    def normalise(values: list[float]) -> list[float]:
        low, high = min(values), max(values)
        if high <= low:
            return [1.0] * len(values)
        return [(value - low) / (high - low) for value in values]
    bge_scores = scores
    bm25_scores = [float(item.get("score", 0.0)) for item in candidates]
    blended_scores = [
        bge_weight * bge + (1 - bge_weight) * bm25
        for bge, bm25 in zip(normalise(bge_scores), normalise(bm25_scores))
    ]
    reranked = [
        {**item, "rerank_score": float(score)}
        for item, score in sorted(
            zip(candidates, blended_scores),
            key=lambda pair: (-float(pair[1]), str(pair[0]["evidence_id"])),
        )
    ]
    return reranked[:top_k]


def _subset_bge_news_cascade(
    corpus: list[dict[str, Any]],
    reranker: Callable[[str, list[str]], list[float]],
    stock_code: str,
    query: str,
    *,
    retrieve_k: int = 20,
    rerank_k: int = 10,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Evaluate a staged ``BM25 Top-20 -> BGE Top-10 -> context Top-5`` path.

    The first stage keeps faceted lexical coverage. BGE then narrows the pool
    to ten candidates. The final stage preserves one title-matched item per
    requested facet where possible before filling by BGE score. This is an
    offline policy experiment; it does not change the production default.
    """
    if not (retrieve_k >= rerank_k >= top_k > 0):
        raise ValueError("cascade must satisfy retrieve_k >= rerank_k >= top_k > 0")
    candidates = _faceted_bm25(corpus, stock_code, query, top_k=retrieve_k)
    if not candidates:
        return []
    scores = _validated_rerank_scores(
        reranker(query, [str(item["content"]) for item in candidates]),
        candidates,
    )
    stage_ten = [
        {**item, "rerank_score": float(score)}
        for item, score in sorted(
            zip(candidates, scores),
            key=lambda pair: (-float(pair[1]), str(pair[0]["evidence_id"])),
        )[:rerank_k]
    ]
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    facets = finance_query_facets(query)
    is_multi_facet = len(facets) > 1 or (facets and facets[0] != expand_finance_query(query))
    if is_multi_facet:
        for facet in facets:
            match = next(
                (item for item in stage_ten if finance_title_matches(str(item.get("title", "")), facet)),
                None,
            )
            if match and _item_identity(match) not in selected_ids:
                selected.append(match)
                selected_ids.add(_item_identity(match))
            if len(selected) >= top_k:
                return selected
    for item in stage_ten:
        identity = _item_identity(item)
        if identity not in selected_ids:
            selected.append(item)
            selected_ids.add(identity)
        if len(selected) >= top_k:
            break
    return selected


def _subset_weighted_hybrid(
    corpus: list[dict[str, Any]],
    vectors: list[list[float]],
    query_embedding: Callable[[list[str]], list[list[float]]],
    stock_code: str,
    query: str,
    *,
    top_k: int,
    bm25_weight: float,
    dense_weight: float,
) -> list[dict[str, Any]]:
    scoped = [item for item in corpus if item["stock_code"] == str(stock_code)]
    by_id = {item["evidence_id"]: vector for item, vector in zip(corpus, vectors)}
    scoped_vectors = [by_id[item["evidence_id"]] for item in scoped]
    bm25 = build_bm25_retriever(scoped)
    dense = build_dense_retriever_from_vectors(scoped, scoped_vectors, query_embedding)
    scores: dict[str, float] = defaultdict(float)
    by_id = {item["evidence_id"]: item for item in scoped}
    for weight, ranked in (
        (bm25_weight, bm25(expand_query(query), top_k=len(scoped))),
        (dense_weight, dense(query, top_k=len(scoped))),
    ):
        for rank, item in enumerate(ranked, start=1):
            scores[item["evidence_id"]] += weight / (60 + rank)
    ranked_ids = sorted(scores, key=lambda value: (-scores[value], value))[:top_k]
    return [{**by_id[evidence_id], "score": scores[evidence_id]} for evidence_id in ranked_ids]


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def build_bge_comparison(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarise BGE deltas against the faceted BM25 diagnostic baseline."""

    baseline_method = "bm25_scoped_faceted"
    baseline_score = results.get(baseline_method, {}).get(
        "keyword_context_recall_diagnostic"
    )
    comparison: dict[str, Any] = {
        "baseline_method": baseline_method,
        "metric": "keyword_context_recall_diagnostic",
        "claim_boundary": (
            "Fixed internal public-news diagnostic only; deltas are not RAGAS, "
            "answer accuracy or production-quality claims."
        ),
        "methods": {},
    }
    if baseline_score is None:
        return comparison
    for method, metrics in results.items():
        if "bge" not in method:
            continue
        score = metrics.get("keyword_context_recall_diagnostic")
        comparison["methods"][method] = {
            "candidate": score,
            "baseline": baseline_score,
            "delta": round(float(score) - float(baseline_score), 4)
            if score is not None
            else None,
        }
    return comparison


def run(
    *,
    port: int,
    embedding_model: str,
    reranker_model: str,
    top_k: int,
    candidate_k: int,
    source_kinds: set[str] | None = None,
    evidence_mode: str = "online",
    bm25_only: bool = False,
) -> dict[str, Any]:
    corpus = load_remote_corpus(
        port,
        source_kinds=source_kinds,
        evidence_mode=evidence_mode,
    )
    print(f"remote_corpus={len(corpus)}", flush=True)
    bm25_global = build_bm25_retriever(corpus)
    methods = {
        "bm25_global_expanded": lambda stock_code, query: _dedupe_source_documents(
            bm25_global(expand_query(query), top_k=len(corpus)), top_k
        ),
        "bm25_scoped_expanded": lambda stock_code, query: _subset_bm25(
            corpus, stock_code, query, top_k=top_k
        ),
        "bm25_scoped_faceted": lambda stock_code, query: _faceted_bm25(
            corpus, stock_code, query, top_k=top_k
        ),
    }
    embedding_meta: dict[str, Any] = {"skipped": True}
    rerank_meta: dict[str, Any] = {"skipped": True}
    vectors: list[list[float]] = []
    embedding = dense_global = hybrid_global = rerank = None
    if not bm25_only:
        embedding, query_embedding, embedding_meta = load_embedding_backend(embedding_model)
        vectors = embedding([item["content"] for item in corpus])
        print(f"dense_vectors={len(vectors)} dimension={len(vectors[0]) if vectors else 0}", flush=True)
        dense_global = build_dense_retriever_from_vectors(corpus, vectors, query_embedding)
        hybrid_global = build_hybrid_rrf_retriever(corpus, bm25_global, dense_global)
        methods.update(
            {
                "dense_global": lambda stock_code, query: dense_global(query, top_k=top_k),
                "hybrid_global": lambda stock_code, query: hybrid_global(query, top_k=top_k),
                "dense_scoped": lambda stock_code, query: _subset_retriever(corpus, vectors, query_embedding, stock_code, query, "dense_scoped", top_k=top_k),
                "hybrid_scoped": lambda stock_code, query: _subset_retriever(corpus, vectors, query_embedding, stock_code, query, "hybrid_scoped", top_k=top_k),
            }
        )

        rerank, rerank_meta = load_reranker(reranker_model)
        methods["bm25_scoped_bge_reranked"] = lambda stock_code, query: _subset_bge_news_reranked(
            corpus, rerank, stock_code, query, top_k=top_k, candidate_k=candidate_k,
        )
        methods["bm25_scoped_bge_bm25_blended"] = lambda stock_code, query: _subset_bge_news_reranked(
            corpus, rerank, stock_code, query, top_k=top_k, candidate_k=candidate_k, bge_weight=0.5,
        )
        if top_k == 5:
            methods["bm25_scoped_bge_20_10_5_faceted"] = lambda stock_code, query: _subset_bge_news_cascade(
                corpus, rerank, stock_code, query, retrieve_k=20, rerank_k=10, top_k=5,
            )
        reranked_global = build_reranked_retriever(hybrid_global, rerank, candidate_k=candidate_k)
        methods["hybrid_global_reranked"] = lambda stock_code, query: reranked_global(query, top_k=top_k)
        methods["hybrid_scoped_reranked"] = lambda stock_code, query: _subset_reranked_retriever(
            corpus, vectors, query_embedding, rerank, stock_code, query,
            top_k=top_k, candidate_k=candidate_k,
        )
        methods["hybrid_scoped_reranked_preserve3"] = lambda stock_code, query: _subset_reranked_retriever(
            corpus, vectors, query_embedding, rerank, stock_code, query,
            top_k=top_k, candidate_k=candidate_k, preserve_k=3,
        )
        methods["hybrid_scoped_bm25_2x"] = lambda stock_code, query: _subset_weighted_hybrid(
            corpus, vectors, query_embedding, stock_code, query,
            top_k=top_k, bm25_weight=2.0, dense_weight=1.0,
        )
        methods["hybrid_scoped_bm25_3x"] = lambda stock_code, query: _subset_weighted_hybrid(
            corpus, vectors, query_embedding, stock_code, query,
            top_k=top_k, bm25_weight=3.0, dense_weight=1.0,
        )

    details: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case_index, case in enumerate(EVAL_DATASET, 1):
        query = case["question"]
        for method, retrieve in methods.items():
            retrieved = retrieve(case["stock_code"], query)
            contexts = [
                news_evidence_snippet(
                    str(item.get("title", "")),
                    str(item["content"]),
                    query,
                )
                for item in retrieved
            ]
            details[method].append(
                {
                    "stock_code": case["stock_code"],
                    "question": query,
                    "contexts": contexts,
                    "result_ids": [item["evidence_id"] for item in retrieved],
                    "titles": [item.get("title", "") for item in retrieved],
                    "keyword_context_recall_diagnostic": manual_context_recall(contexts, case["ground_truth"]),
                }
            )
        print(f"retrieved={case_index}/{len(EVAL_DATASET)}", flush=True)

    result: dict[str, Any] = {
        "dataset": "internal_news_eval_v1",
        "cases": len(EVAL_DATASET),
        "corpus_rows": len(corpus),
        "top_k": top_k,
        "candidate_k": candidate_k,
        "source_kinds": sorted(source_kinds) if source_kinds else ["all"],
        "source_counts": dict(
            sorted(
                (kind, sum(1 for item in corpus if item["source_kind"] == kind))
                for kind in {item["source_kind"] for item in corpus}
            )
        ),
        "evidence_mode": evidence_mode,
        "bm25_only": bm25_only,
        "embedding_runtime": embedding_meta,
        "reranker_runtime": rerank_meta,
        "results": {},
    }
    for method, rows in details.items():
        result["results"][method] = {
            "keyword_context_recall_diagnostic": _mean(
                [float(row["keyword_context_recall_diagnostic"]) for row in rows]
            ),
            "details": rows,
        }

    # Keep the BGE decision visible in the same artifact as the retrieval
    # scores. This avoids making a reviewer manually subtract values from
    # separate JSON files and keeps the fixed-set claim boundary explicit.
    result["bge_comparison"] = build_bge_comparison(result["results"])

    # Let the caller reuse the exact contexts for LLM/RAGAS evaluation without
    # querying the live database again.
    result["ragas_samples"] = {
        method: [
            {
                "question": row["question"],
                "contexts": row["contexts"],
                "ground_truth": next(
                    case["ground_truth"]
                    for case in EVAL_DATASET
                    if case["question"] == row["question"]
                ),
            }
            for row in rows
        ]
        for method, rows in details.items()
    }
    del rerank, dense_global, hybrid_global, vectors, embedding
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("RAG_DB_PORT", "15432")))
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--reranker-model", default=DEFAULT_RERANKER_MODEL)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--source-kinds", default="", help="comma-separated source kinds; empty means all")
    parser.add_argument("--evidence-mode", choices=("online", "title", "full"), default="online")
    parser.add_argument("--bm25-only", action="store_true", help="skip dense embedding and reranker loading")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    source_kinds = {value.strip() for value in args.source_kinds.split(",") if value.strip()}
    report = run(
        port=args.port,
        embedding_model=args.embedding_model,
        reranker_model=args.reranker_model,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        source_kinds=source_kinds or None,
        evidence_mode=args.evidence_mode,
        bm25_only=args.bm25_only,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"out={args.out}", flush=True)
    for method, summary in report["results"].items():
        print(method, summary["keyword_context_recall_diagnostic"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
