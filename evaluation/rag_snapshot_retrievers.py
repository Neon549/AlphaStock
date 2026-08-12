"""Reproducible offline retrievers for a versioned Golden Set corpus snapshot."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import jieba


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "evaluation" / "fixtures" / "rag_corpus_v1.jsonl"
_TOKEN = re.compile(r"[a-z0-9.]+", re.I)
_CHINESE_FRAGMENT = re.compile(r"[\u4e00-\u9fff]+")


def load_snapshot(path: Path = DEFAULT_CORPUS) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_bm25_retriever(corpus: list[dict[str, Any]]) -> Callable[..., list[dict[str, Any]]]:
    """A dependency-free BM25 adapter used only for fixed Golden Set scoring."""
    tokenized = [_tokens(item["content"]) for item in corpus]
    term_frequencies = [Counter(document) for document in tokenized]
    document_frequency: dict[str, int] = {}
    for document in tokenized:
        for token in set(document):
            document_frequency[token] = document_frequency.get(token, 0) + 1
    average_length = sum(len(item) for item in tokenized) / max(len(tokenized), 1)

    def retrieve(query: str, *, top_k: int) -> list[dict[str, Any]]:
        terms = set(_tokens(query))
        scores: list[tuple[float, int]] = []
        for index, document in enumerate(tokenized):
            term_frequency = term_frequencies[index]
            score = 0.0
            for term in terms:
                frequency = term_frequency.get(term, 0)
                if not frequency:
                    continue
                idf = math.log(1 + (len(corpus) - document_frequency.get(term, 0) + 0.5) / (document_frequency.get(term, 0) + 0.5))
                score += idf * frequency * 2.5 / (frequency + 1.5 * (0.25 + 0.75 * len(document) / max(average_length, 1)))
            scores.append((score, index))
        return _ranked(corpus, scores, top_k, minimum_score=0.0)

    return retrieve


def build_dense_retriever(
    corpus: list[dict[str, Any]],
    embedding: Callable[[list[str]], list[list[float]]],
    query_embedding: Callable[[list[str]], list[list[float]]] | None = None,
) -> Callable[..., list[dict[str, Any]]]:
    """Cosine dense adapter; embedding is injected to keep unit tests offline."""
    vectors = embedding([item["content"] for item in corpus])
    encode_query = query_embedding or embedding

    def retrieve(query: str, *, top_k: int) -> list[dict[str, Any]]:
        query_vector = encode_query([query])[0]
        return _ranked(corpus, [(_cosine(query_vector, vector), index) for index, vector in enumerate(vectors)], top_k)

    return retrieve


def build_hybrid_rrf_retriever(
    corpus: list[dict[str, Any]],
    bm25: Callable[..., list[dict[str, Any]]],
    dense: Callable[..., list[dict[str, Any]]],
    *,
    rrf_k: int = 60,
) -> Callable[..., list[dict[str, Any]]]:
    """Fuse rank lists with RRF, returning original snapshot evidence records."""
    by_id = {item["evidence_id"]: item for item in corpus}

    def retrieve(query: str, *, top_k: int) -> list[dict[str, Any]]:
        scores: dict[str, float] = {}
        for ranked in (bm25(query, top_k=len(corpus)), dense(query, top_k=len(corpus))):
            for rank, item in enumerate(ranked, start=1):
                evidence_id = item["evidence_id"]
                scores[evidence_id] = scores.get(evidence_id, 0.0) + 1 / (rrf_k + rank)
        ranked_ids = sorted(scores, key=lambda value: (-scores[value], value))[:top_k]
        return [{**by_id[value], "score": scores[value]} for value in ranked_ids]

    return retrieve


def _tokens(text: str) -> list[str]:
    """Tokenise Latin/numeric terms and Chinese text for a real BM25 baseline."""

    tokens = _TOKEN.findall(text.lower())
    for fragment in _CHINESE_FRAGMENT.findall(text):
        tokens.extend(token for token in jieba.lcut(fragment) if token.strip())
    return tokens


def _ranked(
    corpus: list[dict[str, Any]],
    scores: list[tuple[float, int]],
    top_k: int,
    *,
    minimum_score: float | None = None,
) -> list[dict[str, Any]]:
    ranked = sorted(scores, key=lambda item: (-item[0], corpus[item[1]]["evidence_id"]))
    if minimum_score is not None:
        # BM25 zero means no lexical evidence. Returning it would turn an
        # honest abstention case into a fabricated document answer.
        ranked = [item for item in ranked if item[0] > minimum_score]
    return [{**corpus[index], "score": score} for score, index in ranked[:top_k]]


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0
