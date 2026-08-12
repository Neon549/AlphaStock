"""News retrieval backed by PostgreSQL + pgvector, BM25 and RRF.

The runtime previously imported a Chroma strategy collection for the news
fallback.  That collection was unrelated to the current news request and its
query result was never used for ranking.  This module now keeps one source of
truth: live AKShare news plus the bounded, persisted ``news_vectors`` index.
"""

from __future__ import annotations

import math
import hashlib
from collections import defaultdict

import jieba
from langchain_core.tools import tool

from rag.news_indexer import retrieve_news
from tools.akshare_tools import get_stock_news


class SimpleBM25:
    """Small in-process BM25 scorer for the current live-news candidate set."""

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.tokenized = [list(jieba.cut(doc)) for doc in corpus]
        self.avg_dl = sum(len(doc) for doc in self.tokenized) / max(len(self.tokenized), 1)
        self.df: defaultdict[str, int] = defaultdict(int)
        self.tf: list[defaultdict[str, int]] = []
        for tokens in self.tokenized:
            frequencies: defaultdict[str, int] = defaultdict(int)
            for token in tokens:
                frequencies[token] += 1
            self.tf.append(frequencies)
            for token in set(tokens):
                self.df[token] += 1

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        query_tokens = list(jieba.cut(query))
        scored: list[tuple[int, float]] = []
        count = len(self.tokenized)
        for index, tokens in enumerate(self.tokenized):
            score = 0.0
            for token in query_tokens:
                frequency = self.tf[index].get(token, 0)
                if not frequency:
                    continue
                document_frequency = self.df.get(token, 0)
                idf = math.log((count - document_frequency + 0.5) / (document_frequency + 0.5) + 1)
                score += idf * frequency * (self.k1 + 1) / (
                    frequency + self.k1 * (1 - self.b + self.b * len(tokens) / self.avg_dl)
                )
            scored.append((index, score))
        return sorted(scored, key=lambda item: item[1], reverse=True)[:k]


def rrf_merge(vector_ranked: list[str], bm25_ranked: list[str], k: int = 60) -> list[str]:
    """Fuse semantic and lexical rankings without manually tuned weights."""

    return [document for document, _ in _rrf_ranked(vector_ranked, bm25_ranked, k=k)]


def _rrf_ranked(vector_ranked: list[str], bm25_ranked: list[str], k: int = 60) -> list[tuple[str, float]]:
    """Return RRF order with scores for private retrieval telemetry."""
    scores: defaultdict[str, float] = defaultdict(float)
    for rank, document in enumerate(vector_ranked):
        scores[document] += 1.0 / (rank + k)
    for rank, document in enumerate(bm25_ranked):
        scores[document] += 1.0 / (rank + k)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _rank_by_token_overlap(news_items: list[str], query: str) -> list[str]:
    query_tokens = set(jieba.cut(query))
    return [
        item
        for item, _ in sorted(
            ((item, len(query_tokens & set(jieba.cut(item)))) for item in news_items),
            key=lambda pair: pair[1],
            reverse=True,
        )
    ]


def _pgvector_candidates(stock_code: str, query: str, top_k: int) -> list[str]:
    try:
        result = retrieve_news(query=query, stock_code=stock_code, k=top_k * 2, days=7)
        if "[TOOL_ERROR]" in result or "未找到相关新闻" in result:
            return []
        return _lines(result)
    except Exception:
        # Live AKShare results and lexical retrieval still provide a bounded
        # answer when the persisted index or embedding model is unavailable.
        return []


def hybrid_retrieve_news(stock_code: str, query: str, top_k: int = 5) -> str:
    """Retrieve current news with pgvector semantic candidates and BM25 + RRF."""

    raw_news = get_stock_news.invoke({"symbol": stock_code})
    if "[TOOL_ERROR]" in (raw_news or ""):
        raw_news = ""

    live_items = _lines(raw_news)
    semantic_items = _pgvector_candidates(stock_code, query, top_k)
    if not live_items and not semantic_items:
        _record_news_retrieval(stock_code, query, top_k, [], 0, 0, rerank_applied=False)
        return "暂无可验证的相关新闻"
    if not query:
        selected = (live_items or semantic_items)[:top_k]
        _record_news_retrieval(
            stock_code, query, top_k, [(item, None) for item in selected],
            len(live_items), len(semantic_items), rerank_applied=False,
        )
        return "\n".join(selected)

    # pgvector results are already semantically ranked.  Add current live
    # items ranked by query overlap so fresh news is not hidden by index lag.
    vector_ranked = list(dict.fromkeys(semantic_items + _rank_by_token_overlap(live_items, query)))
    lexical_corpus = list(dict.fromkeys(live_items + semantic_items))
    bm25 = SimpleBM25(lexical_corpus)
    bm25_ranked = [
        lexical_corpus[index]
        for index, score in bm25.search(query, k=len(lexical_corpus))
        if score > 0
    ]
    merged = _rrf_ranked(vector_ranked, bm25_ranked or lexical_corpus)
    selected = merged[:top_k]
    _record_news_retrieval(stock_code, query, top_k, selected, len(live_items), len(semantic_items), rerank_applied=True)
    return "\n".join(item for item, _ in selected) or "暂无可验证的相关新闻"


def _record_news_retrieval(
    stock_code: str,
    query: str,
    top_k: int,
    selected: list[tuple[str, float | None]],
    live_candidate_count: int,
    semantic_candidate_count: int,
    *,
    rerank_applied: bool,
) -> None:
    """Write RAG metadata only; headlines and raw query never leave the process."""
    try:
        from control_plane.observability import record_rag_event, redact_query

        top_results = [
            {
                "rank": rank,
                "news_sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
                "rrf_score": round(score, 8) if score is not None else None,
            }
            for rank, (item, score) in enumerate(selected, start=1)
        ]
        status = "ok" if selected else "abstained"
        record_rag_event("retrieval", {
            "query": redact_query(query),
            "source_kind": "news_hybrid",
            "status": status,
            **({"abstain_reason": "no_retrieval_hits"} if not selected else {}),
            "requested_top_k": top_k,
            "retrieved_chunk_count": len(selected),
            "top_k": top_results,
            "corpus_snapshot": {
                "source_kind": "news_index_and_live_feed",
                "stock_code": stock_code,
                "window_days": 7,
                "live_candidate_count": live_candidate_count,
                "semantic_candidate_count": semantic_candidate_count,
            },
            "rerank": {"applied": rerank_applied, "method": "rrf" if rerank_applied else None},
        })
        record_rag_event("citation_validation", {
            "status": "not_applicable",
            "validation_type": "unstructured_live_news",
            "citation_count": 0,
            "retrieval_status": status,
        })
    except Exception:
        # News availability must not depend on observability configuration.
        return


@tool
def retrieve_stock_news(stock_code: str, query: str = "") -> str:
    """Retrieve stock news using pgvector semantic recall, BM25 and RRF."""

    return hybrid_retrieve_news(stock_code, query)
