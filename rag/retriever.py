"""News retrieval backed by PostgreSQL + pgvector, BM25 and RRF.

The runtime previously imported a Chroma strategy collection for the news
fallback.  That collection was unrelated to the current news request and its
query result was never used for ranking.  This module now keeps one source of
truth: live AKShare news plus the bounded, persisted ``news_vectors`` index.
"""

from __future__ import annotations

import math
import hashlib
import logging
import os
import re
from collections import defaultdict

import jieba
from langchain_core.tools import tool

from rag.news_indexer import retrieve_news, retrieve_news_corpus
from rag.query_rewrite import rewrite_retrieval_query
from tools.stock_name_dict import get_stock_name
from tools.akshare_tools import get_stock_news


NEWS_RETRIEVAL_DAYS = 30
DEFAULT_NEWS_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
# RAGAS showed that a Top-20 pool displaced too much high-precision lexical
# evidence.  Keep BGE as the default second stage, but reorder BM25's Top-5;
# wider candidate pools remain explicit experiments.
NEWS_RERANK_CANDIDATE_K = max(5, int(os.getenv("NEWS_RERANK_CANDIDATE_K", "5")))
NEWS_RERANK_BATCH_SIZE = max(1, int(os.getenv("NEWS_RERANK_BATCH_SIZE", "16")))
# Keep pure Cross-Encoder order as the default.  The 50/50 lexical blend is
# configurable for offline experiments but regressed all four RAGAS metrics.
NEWS_RERANK_BGE_WEIGHT = min(1.0, max(0.0, float(os.getenv("NEWS_RERANK_BGE_WEIGHT", "1.0"))))

logger = logging.getLogger(__name__)
_news_reranker = None
_news_reranker_load_attempted = False

_FINANCE_QUERY_ALIASES: tuple[tuple[str, str], ...] = (
    ("AI服务器", "人工智能服务器 AI server 算力服务器"),
    ("人工智能服务器", "AI服务器 AI server 算力服务器"),
    ("算力", "AI服务器 人工智能 算力中心"),
    ("大宗交易", "大宗交易 block trade bulk trade"),
    ("回购", "股份回购 股票回购 回购注销"),
    ("减持", "股东减持 减持计划 大宗交易"),
    ("增持", "股东增持 买入 持股增加"),
    ("董事长", "董事长 董事 负责人 任职 变更"),
    ("高管", "高管 董事 监事 人事变动"),
    ("净利润", "净利润 归母净利润 业绩利润"),
    ("营收", "营业收入 营收 收入"),
    ("现金流", "经营现金流 现金流量"),
    ("资金流入", "资金流入 主力资金 净流入"),
)

_FINANCE_QUERY_FACETS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("提价", "价格", "调价"), "提价 调价 零售价 合同价 批价 上调 下调"),
    (("业绩", "净利润", "营收"), "业绩 营业收入 净利润 归母净利润 同比增长 预告"),
    (("分红", "派息", "利润分配"), "分红 派息 利润分配 现金红利 权益分派"),
    (("人事", "董事长", "高管", "秘书", "任职"), "人事变动 离任 接任 辞职 聘任 任职资格 董事长 公司秘书"),
    (("资金", "主力", "净流入", "净流出"), "资金流动 主力资金 净流入 净流出 特大单"),
    (("回购",), "股份回购 回购金额 回购价格 回购进展"),
    (("上市", "挂牌", "IPO", "H股"), "上市 挂牌 IPO H股 港交所 联交所"),
    (("新公司", "业务扩展", "新业务", "合作", "订单"), "新公司 子公司 注册资本 成立 业务扩展 合作 订单 中标"),
    (("大宗交易",), "大宗交易 成交额 折价率 block trade"),
    (("重要动态",), "重要动态 大宗交易 光缆 海底电缆 业务进展 资金流动"),
)


def expand_finance_query(query: str) -> str:
    """Add high-signal finance synonyms for lexical retrieval only."""

    expanded = [query.strip()] if query and query.strip() else []
    lowered = query.lower() if query else ""
    for keyword, aliases in _FINANCE_QUERY_ALIASES:
        if keyword.lower() in lowered:
            expanded.append(aliases)
    return " ".join(dict.fromkeys(expanded))


def finance_query_facets(query: str) -> list[str]:
    """Turn multi-intent finance questions into focused lexical subqueries."""

    value = query or ""
    facets = [aliases for triggers, aliases in _FINANCE_QUERY_FACETS if any(term in value for term in triggers)]
    return list(dict.fromkeys(facets)) or [expand_finance_query(value)]


def _finance_tokens(value: str) -> list[str]:
    """Tokenize Chinese finance text while retaining IDs, numbers and chars."""

    compact = "".join((value or "").lower().split())
    words = [token for token in jieba.lcut(compact) if token.strip()]
    chinese_chars = [char for char in compact if "\u4e00" <= char <= "\u9fff"]
    latin_or_numbers = re.findall(r"[a-z]+|\d+(?:\.\d+)?", compact)
    return words + chinese_chars + latin_or_numbers


class SimpleBM25:
    """Small in-process BM25 scorer for the current live-news candidate set."""

    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.tokenized = [_finance_tokens(doc) for doc in corpus]
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
        query_tokens = _finance_tokens(query)
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


def _rrf_ranked(
    vector_ranked: list[str],
    bm25_ranked: list[str],
    k: int = 60,
    *,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[tuple[str, float]]:
    """Return RRF order with scores for private retrieval telemetry."""
    scores: defaultdict[str, float] = defaultdict(float)
    for rank, document in enumerate(vector_ranked):
        scores[document] += vector_weight / (rank + k)
    for rank, document in enumerate(bm25_ranked):
        scores[document] += bm25_weight / (rank + k)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def _lines(value: str) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _filter_live_news_by_entity(stock_code: str, items: list[str]) -> list[str]:
    """Drop live feed listicles that do not identify the requested security.

    The persisted corpus applies the richer, official-alias-aware gate in
    ``retrieve_news_corpus``.  Live rows lack that metadata, so use the local
    canonical name and ticker.  Persisted evidence still fills results when a
    valid story uses a newer short name.
    """

    stock_name = get_stock_name(stock_code)
    entity_names = (stock_code, stock_name)
    return [item for item in items if any(entity_name and entity_name in item for entity_name in entity_names)]


def _document_identity(document: str) -> str:
    """Collapse multiple evidence chunks from the same official disclosure."""

    link = re.search(r"链接：(\S+)", document or "")
    if link:
        return f"source:{link.group(1)}"
    return f"title:{_document_title(document)}"


def _document_title(document: str) -> str:
    announcement = re.search(r"公告：(.*?)(?=\s内容：|\s来源：|$)", document or "")
    if announcement:
        return announcement.group(1).strip()
    if "】" in (document or ""):
        return document.split("】", 1)[1].strip()
    return (document or "").strip()


def _is_official_announcement(document: str) -> bool:
    """Recognize persisted CNInfo evidence without exposing source internals."""

    return "公告：" in (document or "") and "来源：巨潮资讯" in (document or "")


def finance_title_matches(title_or_document: str, facet: str) -> bool:
    """Require a facet's high-signal term in the evidence title."""

    title = _document_title(title_or_document).lower()
    stopwords = {"公司", "业务", "动态", "消息", "相关", "近期", "重要"}
    terms = [term.lower() for term in facet.split() if len(term) > 1 and term not in stopwords]
    return any(term in title for term in terms)


def _unique_bm25_ranking(bm25: SimpleBM25, corpus: list[str], query: str) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    seen: set[str] = set()
    for index, score in bm25.search(query, k=len(corpus)):
        if score <= 0:
            continue
        document = corpus[index]
        identity = _document_identity(document)
        if identity in seen:
            continue
        seen.add(identity)
        ranked.append((document, score))
    return ranked


def _select_faceted_bm25(corpus: list[str], query: str, top_k: int) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Return diversified top-k plus the full primary BM25 ranking."""

    if not corpus or top_k <= 0:
        return [], []
    bm25 = SimpleBM25(corpus)
    primary = _unique_bm25_ranking(bm25, corpus, expand_finance_query(query))
    selected: list[tuple[str, float]] = []
    selected_ids: set[str] = set()
    facets = finance_query_facets(query)
    if len(facets) > 1 or (facets and facets[0] != expand_finance_query(query)):
        for facet in facets:
            for document, score in _unique_bm25_ranking(bm25, corpus, facet):
                identity = _document_identity(document)
                if identity not in selected_ids and finance_title_matches(document, facet):
                    selected.append((document, score))
                    selected_ids.add(identity)
                    break
            if len(selected) >= top_k:
                return selected, primary
    for document, score in primary:
        identity = _document_identity(document)
        if identity not in selected_ids:
            selected.append((document, score))
            selected_ids.add(identity)
        if len(selected) >= top_k:
            break
    return selected, primary


def _get_news_reranker():
    """Load the production BGE cross-encoder once, without network access.

    The model is deliberately lazy: a process serving a request that does not
    need news retrieval does not pay the model's memory or startup cost.  A
    missing local model must not make news retrieval unavailable, so callers
    receive ``None`` and retain the first-stage BM25 ranking.
    """

    global _news_reranker, _news_reranker_load_attempted
    if _news_reranker_load_attempted:
        return _news_reranker
    _news_reranker_load_attempted = True
    try:
        # ``local_files_only`` does not prevent all optional processor-config
        # probes in older Transformers releases.  Force offline mode before
        # importing the model stack so a partial/missing cache fails fast.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        import torch
        from sentence_transformers import CrossEncoder

        device = os.getenv("NEWS_RERANK_DEVICE") or ("cuda" if torch.cuda.is_available() else "cpu")
        model_name = os.getenv("NEWS_RERANKER_MODEL", DEFAULT_NEWS_RERANKER_MODEL)
        model = CrossEncoder(model_name, device=device, local_files_only=True)

        def rerank(query: str, passages: list[str]) -> list[float]:
            pairs = [(query, passage) for passage in passages]
            scores = model.predict(pairs, batch_size=NEWS_RERANK_BATCH_SIZE, show_progress_bar=False)
            return [float(score) for score in scores]

        _news_reranker = rerank
        logger.info("Loaded news reranker model=%s device=%s", model_name, device)
    except Exception as exc:
        logger.warning("BGE news reranker unavailable; using BM25 order: %s", exc)
    return _news_reranker


def _rerank_news_candidates(
    query: str,
    primary_ranked: list[tuple[str, float]],
    facet_selected: list[tuple[str, float]],
    top_k: int,
) -> tuple[list[tuple[str, float]], bool]:
    """Cross-encode a bounded news pool while retaining multi-facet coverage."""

    if not primary_ranked or top_k <= 0:
        return [], False
    candidates: list[str] = []
    bm25_scores: dict[str, float] = {}
    seen: set[str] = set()
    # BGE is a second-stage ordering pass, not a second retrieval policy.  Its
    # default pool is exactly the BM25/facet output that would otherwise reach
    # the answer model.  This preserves lexical topic coverage while allowing
    # the cross-encoder to order those evidence items by query relevance.  A
    # deliberately wider environment override remains an offline experiment.
    candidate_source = (
        facet_selected
        if NEWS_RERANK_CANDIDATE_K <= top_k
        else [*facet_selected, *primary_ranked]
    )
    for document, _ in candidate_source:
        identity = _document_identity(document)
        if identity not in seen:
            candidates.append(document)
            seen.add(identity)
        bm25_scores[identity] = max(bm25_scores.get(identity, float("-inf")), float(_))
        if len(candidates) >= max(top_k, NEWS_RERANK_CANDIDATE_K):
            break

    reranker = _get_news_reranker()
    if reranker is None:
        return facet_selected[:top_k], False
    try:
        scores = reranker(query, candidates)
    except Exception as exc:
        logger.warning("BGE news reranking failed; using BM25 order: %s", exc)
        return facet_selected[:top_k], False
    if len(scores) != len(candidates):
        logger.warning("BGE news reranker returned %s scores for %s candidates", len(scores), len(candidates))
        return facet_selected[:top_k], False
    try:
        scores = [float(score) for score in scores]
    except (TypeError, ValueError):
        logger.warning("BGE news reranker returned a non-numeric score; using BM25 order")
        return facet_selected[:top_k], False
    if not all(math.isfinite(score) for score in scores):
        logger.warning("BGE news reranker returned a non-finite score; using BM25 order")
        return facet_selected[:top_k], False

    def normalise(values: list[float]) -> list[float]:
        low, high = min(values), max(values)
        if high <= low:
            return [1.0] * len(values)
        return [(value - low) / (high - low) for value in values]

    bge_normalised = normalise(scores)
    lexical_normalised = normalise([bm25_scores[_document_identity(document)] for document in candidates])
    reranked = sorted(
        zip(candidates, scores, bge_normalised, lexical_normalised, range(len(candidates))),
        key=lambda item: (-(NEWS_RERANK_BGE_WEIGHT * item[2] + (1 - NEWS_RERANK_BGE_WEIGHT) * item[3]), item[4]),
    )
    selected: list[tuple[str, float]] = []
    selected_ids: set[str] = set()
    facets = finance_query_facets(query)
    is_multi_facet = len(facets) > 1 or (facets and facets[0] != expand_finance_query(query))
    if is_multi_facet:
        for facet in facets:
            match = next(
                ((document, float(score)) for document, score, _, _, _ in reranked if finance_title_matches(document, facet)),
                None,
            )
            if match and _document_identity(match[0]) not in selected_ids:
                selected.append(match)
                selected_ids.add(_document_identity(match[0]))
            if len(selected) >= top_k:
                return selected, True
    for document, score, _, _, _ in reranked:
        identity = _document_identity(document)
        if identity not in selected_ids:
            selected.append((document, float(score)))
            selected_ids.add(identity)
        if len(selected) >= top_k:
            break
    return selected, True


def _rank_by_token_overlap(news_items: list[str], query: str) -> list[str]:
    query_tokens = set(_finance_tokens(query))
    return [
        item
        for item, _ in sorted(
            ((item, len(query_tokens & set(_finance_tokens(item)))) for item in news_items),
            key=lambda pair: pair[1],
            reverse=True,
        )
    ]


def _pgvector_candidates(stock_code: str, query: str, top_k: int, *, days: int = NEWS_RETRIEVAL_DAYS) -> list[str]:
    try:
        result = retrieve_news(
            query=query,
            stock_code=stock_code,
            k=top_k,
            days=days,
        )
        if "[TOOL_ERROR]" in result or "未找到相关新闻" in result:
            return []
        return _lines(result)
    except Exception:
        # Live AKShare results and lexical retrieval still provide a bounded
        # answer when the persisted index or embedding model is unavailable.
        return []


def _scoped_lexical_candidates(stock_code: str, *, days: int = NEWS_RETRIEVAL_DAYS) -> list[str]:
    try:
        return retrieve_news_corpus(
            stock_code,
            days=days,
            limit=500,
        )
    except Exception:
        return []


def hybrid_retrieve_news(stock_code: str, query: str, top_k: int = 5) -> str:
    """Retrieve stock-scoped BM25 evidence with lazy semantic fallback."""

    # The retrieval scope is the resolved task entity.  Treat it as inherited
    # context so a ticker explicitly written in a follow-up keeps precedence
    # in the auditable rewrite plan.
    rewrite = rewrite_retrieval_query(query, context_stock_code=stock_code)
    retrieval_query = rewrite["rewritten_query"]
    news_days = int(rewrite["filters"].get("news_days", NEWS_RETRIEVAL_DAYS))

    raw_news = get_stock_news.invoke({"symbol": stock_code})
    if "[TOOL_ERROR]" in (raw_news or ""):
        raw_news = ""

    live_items = _filter_live_news_by_entity(stock_code, _lines(raw_news))
    scoped_lexical_items = _scoped_lexical_candidates(stock_code, days=news_days)
    if not query:
        selected = (live_items or scoped_lexical_items)[:top_k]
        _record_news_retrieval(
            stock_code, query, top_k, [(item, None) for item in selected],
            len(live_items), 0,
            lexical_candidate_count=len(scoped_lexical_items), rerank_applied=False,
            rewrite=rewrite,
        )
        return "\n".join(selected) or "暂无可验证的相关新闻"

    # Rank the complete bounded stock corpus lexically.  Dense retrieval is a
    # fallback/fill path because the current remote snapshot showed materially
    # better Recall and Precision for stock-scoped title BM25.
    lexical_corpus = list(dict.fromkeys(live_items + scoped_lexical_items))
    news_corpus = [item for item in lexical_corpus if not _is_official_announcement(item)]
    announcement_corpus = [item for item in lexical_corpus if _is_official_announcement(item)]
    lexical_selected, bm25_ranked = _select_faceted_bm25(news_corpus, retrieval_query, top_k)
    selected, rerank_applied = _rerank_news_candidates(
        retrieval_query,
        bm25_ranked,
        lexical_selected,
        top_k,
    )
    if len(selected) < top_k:
        announcement_selected, announcement_ranked = _select_faceted_bm25(
            announcement_corpus,
            retrieval_query,
            top_k - len(selected),
        )
        existing_ids = {_document_identity(item) for item, _ in selected}
        for item, score in announcement_selected:
            if _document_identity(item) not in existing_ids:
                selected.append((item, score))
                existing_ids.add(_document_identity(item))
        bm25_ranked.extend(announcement_ranked)
    selected_ids = {_document_identity(item) for item, _ in selected}
    semantic_items: list[str] = []
    if len(selected) < top_k:
        semantic_candidate_k = max(top_k * 4, 20)
        semantic_items = _pgvector_candidates(stock_code, retrieval_query, semantic_candidate_k, days=news_days)
        for item in semantic_items:
            identity = _document_identity(item)
            if identity not in selected_ids:
                selected.append((item, None))
                selected_ids.add(identity)
            if len(selected) >= top_k:
                break
    _record_news_retrieval(
        stock_code,
        query,
        top_k,
        selected,
        len(live_items),
        len(semantic_items),
        lexical_candidate_count=len(scoped_lexical_items),
        rerank_applied=rerank_applied,
        rerank_method="bge_cross_encoder_news" if rerank_applied else "scoped_title_bm25_fallback",
        rewrite=rewrite,
    )
    return "\n".join(item for item, _ in selected) or "暂无可验证的相关新闻"


def _record_news_retrieval(
    stock_code: str,
    query: str,
    top_k: int,
    selected: list[tuple[str, float | None]],
    live_candidate_count: int,
    semantic_candidate_count: int,
    *,
    lexical_candidate_count: int = 0,
    rerank_applied: bool,
    rerank_method: str | None = None,
    rewrite: dict[str, object] | None = None,
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
                "window_days": int((rewrite or {}).get("filters", {}).get("news_days", NEWS_RETRIEVAL_DAYS)),
                "live_candidate_count": live_candidate_count,
                "semantic_candidate_count": semantic_candidate_count,
                "lexical_candidate_count": lexical_candidate_count,
            },
            "rerank": {
                "applied": rerank_applied,
                "method": rerank_method,
                "model": os.getenv("NEWS_RERANKER_MODEL", DEFAULT_NEWS_RERANKER_MODEL) if rerank_applied else None,
                "candidate_k": NEWS_RERANK_CANDIDATE_K if rerank_applied else 0,
                "bge_weight": NEWS_RERANK_BGE_WEIGHT if rerank_applied else None,
            },
            "query_rewrite": {
                "applied": bool((rewrite or {}).get("applied")),
                "source": (rewrite or {}).get("rewrite_source"),
                "reason": (rewrite or {}).get("rewrite_reason", []),
                "filters": (rewrite or {}).get("filters", {}),
                "rewritten_query": redact_query(str((rewrite or {}).get("rewritten_query") or query)),
            },
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
