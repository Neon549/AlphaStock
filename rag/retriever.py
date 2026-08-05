"""News retrieval backed by PostgreSQL + pgvector, BM25 and RRF.

The runtime previously imported a Chroma strategy collection for the news
fallback.  That collection was unrelated to the current news request and its
query result was never used for ranking.  This module now keeps one source of
truth: live AKShare news plus the bounded, persisted ``news_vectors`` index.
"""

from __future__ import annotations

import math
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

    scores: defaultdict[str, float] = defaultdict(float)
    for rank, document in enumerate(vector_ranked):
        scores[document] += 1.0 / (rank + k)
    for rank, document in enumerate(bm25_ranked):
        scores[document] += 1.0 / (rank + k)
    return sorted(scores, key=scores.get, reverse=True)


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
        return "暂无可验证的相关新闻"
    if not query:
        return "\n".join((live_items or semantic_items)[:top_k])

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
    merged = rrf_merge(vector_ranked, bm25_ranked or lexical_corpus)
    return "\n".join(merged[:top_k]) or "暂无可验证的相关新闻"


@tool
def retrieve_stock_news(stock_code: str, query: str = "") -> str:
    """Retrieve stock news using pgvector semantic recall, BM25 and RRF."""

    return hybrid_retrieve_news(stock_code, query)
