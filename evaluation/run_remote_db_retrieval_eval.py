"""Evaluate BM25/Dense/Hybrid/Rerank against the remote PostgreSQL news index.

The database is reached through a local SSH tunnel.  This runner intentionally
keeps the database read-only and writes only local runtime reports.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
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


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "runtime" / "reports" / "remote-db-retrieval-ablation.json"
DEFAULT_EMBEDDING_MODEL = "bge_small_zh_v1_5_no_instruction"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

ALIASES = {
    "提价": "调价 零售价 合同价 批价 上调",
    "价格": "调价 零售价 合同价 批价 上调 下调",
    "新公司": "成立 子公司 注册资本 新能源 物联网",
    "业务扩展": "成立 子公司 合作 订单 注册资本",
    "业绩": "净利润 净利 半年报 同比增长",
    "分红": "派息 利润分配 中期分红 现金红利",
    "人事变动": "离任 接任 任职 董事长 秘书",
    "资金流动": "主力资金 净流入 净流出 特大单 资金动向",
    "新业务": "成立 合作 订单 子公司",
    "合作": "订单 成立 子公司",
    "回购": "回购股份 回购金额 回购价格",
    "上市": "港股 IPO 挂牌 联交所",
    "重要动态": "回购 质押 成交 订单 资金动向",
}


def expand_query(query: str) -> str:
    additions = [value for key, value in ALIASES.items() if key in query]
    return f"{query} {' '.join(additions)}" if additions else query


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


def load_remote_corpus(port: int) -> list[dict[str, Any]]:
    with _remote_connection(port) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT stock_code, title, stock_name, pub_time, date
            FROM news_vectors
            ORDER BY date DESC, pub_time DESC
            """
        )
        rows = []
        seen: set[tuple[str, str, str]] = set()
        for stock_code, title, stock_name, pub_time, date in cur.fetchall():
            stock_code = str(stock_code)
            title = str(title or "").strip()
            key = (stock_code, title, str(date))
            if not title or key in seen:
                continue
            seen.add(key)
            evidence_id = "news:" + hashlib.sha256(
                f"{stock_code}|{date}|{pub_time}|{title}".encode("utf-8")
            ).hexdigest()[:24]
            text = f"[{pub_time}] {stock_name}({stock_code}) {title}"
            rows.append(
                {
                    "evidence_id": evidence_id,
                    "content": text,
                    "text": text,
                    "stock_code": stock_code,
                    "stock_name": str(stock_name or ""),
                    "title": title,
                    "pub_time": str(pub_time or ""),
                    "date": str(date or ""),
                }
            )
    return rows


def load_embedding_backend(name: str):
    from evaluation.run_candidate_rag_eval import load_local_embedding_backend

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    return load_local_embedding_backend(name)


def load_reranker(model_name: str):
    import torch
    from sentence_transformers import CrossEncoder

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CrossEncoder(model_name, device=device, local_files_only=True)

    def rerank(query: str, passages: list[str]) -> list[float]:
        pairs = [(query, passage) for passage in passages]
        scores = model.predict(pairs, batch_size=32, show_progress_bar=False)
        return [float(score) for score in scores]

    return rerank, {"model": model_name, "device": device}


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
    scores = reranker(query, [str(item["content"]) for item in candidates])
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


def run(*, port: int, embedding_model: str, reranker_model: str, top_k: int, candidate_k: int) -> dict[str, Any]:
    corpus = load_remote_corpus(port)
    print(f"remote_corpus={len(corpus)}", flush=True)
    embedding, query_embedding, embedding_meta = load_embedding_backend(embedding_model)
    vectors = embedding([item["content"] for item in corpus])
    print(f"dense_vectors={len(vectors)} dimension={len(vectors[0]) if vectors else 0}", flush=True)

    bm25_global = build_bm25_retriever(corpus)
    dense_global = build_dense_retriever_from_vectors(corpus, vectors, query_embedding)
    hybrid_global = build_hybrid_rrf_retriever(corpus, bm25_global, dense_global)
    methods = {
        "bm25_global_expanded": lambda stock_code, query: bm25_global(expand_query(query), top_k=top_k),
        "dense_global": lambda stock_code, query: dense_global(query, top_k=top_k),
        "hybrid_global": lambda stock_code, query: hybrid_global(query, top_k=top_k),
        "bm25_scoped_expanded": lambda stock_code, query: _subset_retriever(corpus, vectors, query_embedding, stock_code, query, "bm25_scoped_expanded", top_k=top_k),
        "dense_scoped": lambda stock_code, query: _subset_retriever(corpus, vectors, query_embedding, stock_code, query, "dense_scoped", top_k=top_k),
        "hybrid_scoped": lambda stock_code, query: _subset_retriever(corpus, vectors, query_embedding, stock_code, query, "hybrid_scoped", top_k=top_k),
    }

    rerank, rerank_meta = load_reranker(reranker_model)
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
            contexts = [str(item["content"]) for item in retrieved]
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
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    report = run(
        port=args.port,
        embedding_model=args.embedding_model,
        reranker_model=args.reranker_model,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"out={args.out}", flush=True)
    for method, summary in report["results"].items():
        print(method, summary["keyword_context_recall_diagnostic"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
