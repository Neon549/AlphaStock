"""Evaluate a human-review candidate RAG set against a pinned public corpus."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
from pathlib import Path
from typing import Any

from evaluation.download_corpus import DEFAULT_SOURCE_MANIFEST, load_sources
from evaluation.rag_golden_eval import citation_matches, evaluate_retrieval_cases, load_cases
from evaluation.rag_snapshot_retrievers import (
    build_bm25_retriever,
    build_dense_retriever,
    build_hybrid_rrf_retriever,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "rag_candidates.jsonl"
DEFAULT_CHUNKS = ROOT / "runtime" / "reports" / "public-filings-candidate-v1.chunks.jsonl"
EMBEDDING_BACKENDS = {
    "project_text2vec": {
        "model": "shibing624/text2vec-base-chinese",
        "query_instruction": "",
    },
    "bge_small_zh_v1_5": {
        "model": "BAAI/bge-small-zh-v1.5",
        "query_instruction": "为这个句子生成表示以用于检索相关文章：",
    },
    "bge_small_zh_v1_5_no_instruction": {
        "model": "BAAI/bge-small-zh-v1.5",
        "query_instruction": "",
    },
    "bge_m3": {
        "model": "BAAI/bge-m3",
        "query_instruction": "",
        "batch_size": 16,
    },
    "bge_small_en_v1_5": {
        # FinanceBench questions and SEC filings are English. This compact
        # model makes its external-benchmark Dense/RRF run practical on CPU.
        "model": "BAAI/bge-small-en-v1.5",
        "query_instruction": "Represent this sentence for searching relevant passages: ",
        "batch_size": 128,
    },
}
FACT_CONTEXT_ALIASES = {
    "revenue": ("营业收入",),
    "operating_cash_flow": ("经营活动产生的现金流量净额",),
    "net_profit_attributable": (
        "归属于上市公司股东的净利润",
        "归属于本行股东的净利润",
        "归属于母公司股东的净利润",
    ),
    "non_performing_loan_ratio": ("不良贷款率",),
    "net_interest_margin": ("净息差",),
}


def load_candidate_corpus(
    path: Path = DEFAULT_CHUNKS,
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
) -> list[dict[str, Any]]:
    sources = {
        str(document["document_id"]): document
        for document in load_sources(source_manifest)["documents"]
    }
    corpus: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        source = sources[str(row["document_id"])]
        corpus.append(
            {
                **row,
                # Retrieval constraints are source metadata, not properties of
                # the parser chunk.  Preserve them on every chunk so company,
                # security-code and report-period scoping works for candidate
                # and final evaluation corpora alike.
                "security_code": source["security_code"],
                "report_period": source["report_period"],
                "company": source["company"],
                "filename": f"{row['document_id']}.pdf",
                "section": " / ".join(row.get("parent_path", [])),
                "content": row["text"],
            }
        )
    if not corpus:
        raise ValueError("candidate corpus has no chunks")
    return corpus


def _scoped_corpus(query: str, corpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply deterministic entity/report-period constraints before ranking."""

    scoped = corpus
    companies = {str(item["company"]) for item in corpus if item.get("company")}
    company_matches = [company for company in companies if company in query]
    code_matches = re.findall(r"(?<!\d)(?:[036]\d{5}|688\d{3})(?!\d)", query)
    if len(company_matches) == 1:
        scoped = [item for item in scoped if item.get("company") == company_matches[0]]
    elif len(set(code_matches)) == 1:
        scoped = [item for item in scoped if item.get("security_code") == code_matches[0]]

    years = set(re.findall(r"20\d{2}", query))
    if len(years) == 1:
        year = next(iter(years))
        scoped = [item for item in scoped if year in str(item.get("report_period", ""))]

    period_markers = {
        "Q1": ("第一季度", "一季度", "Q1", "q1"),
        "Q2": ("第二季度", "二季度", "Q2", "q2"),
        "Q3": ("第三季度", "三季度", "Q3", "q3"),
        "Q4": ("第四季度", "四季度", "Q4", "q4"),
        "H1": ("半年度", "上半年", "H1", "h1"),
        "FY": ("年度报告", "年报", "全年", "FY", "fy"),
    }
    matched_periods = [period for period, markers in period_markers.items() if any(marker in query for marker in markers)]
    if len(matched_periods) == 1:
        expected_period = matched_periods[0]
        scoped = [item for item in scoped if expected_period in str(item.get("report_period", "")).upper()]
    return scoped


def _intent_terms(query: str) -> tuple[str, ...]:
    """Expand common finance aliases for sparse lexical retrieval.

    These are deterministic retrieval terms, not answer facts and not an LLM
    rewrite.  They make users' wording (归母、现金、存货) match the disclosure
    table labels while preserving the original query in the BM25 input.
    """

    aliases = {
        "归母净利润": ("归属于上市公司股东的净利润",),
        "归母": ("归属于上市公司股东",),
        "经营活动现金流": ("经营活动产生的现金流量净额",),
        "经营现金流": ("经营活动产生的现金流量净额",),
        "现金流净额": ("经营活动产生的现金流量净额",),
        "货币资金": ("货币资金",),
        "现金": ("货币资金",),
        "存货余额": ("存货",),
        "存货": ("存货",),
        "研发费用": ("研发费用",),
        "研发投入": ("研发投入",),
        "基本每股收益": ("基本每股收益",),
        "每股收益": ("基本每股收益",),
        "净资产收益率": ("加权平均净资产收益率",),
        "总资产": ("总资产",),
        "生产部门": ("生产人员", "专业构成", "生产人员数量"),
        "员工": ("在职员工", "职工人数", "专业构成人数"),
        "多少员工": ("数量（人）", "人数"),
        "可转债": ("转债", "募集资金", "募投项目"),
        "募集资金使用": ("募集资金用途", "募投项目", "募集资金投资项目"),
        "使用情况": ("投入募集资金", "募集资金使用情况", "项目建设"),
        "库存股余额": ("库存股", "期末余额", "回购库存股"),
        "第一大业务板块": ("主营业务分行业", "主营业务分产品", "收入构成"),
        "业务板块": ("主营业务分行业", "主营业务分产品", "收入构成"),
    }
    return tuple(term for trigger, terms in aliases.items() if trigger in query for term in terms)


def _expanded_query(query: str) -> str:
    terms = _intent_terms(query)
    return " ".join((query, *terms)) if terms else query


def build_scoped_bm25_retriever(corpus: list[dict[str, Any]], *, expand_query: bool = False):
    cache: dict[tuple[str, ...], Any] = {}

    def retrieve(query: str, *, top_k: int) -> list[dict[str, Any]]:
        scoped = _scoped_corpus(query, corpus)
        if not scoped:
            return []
        key = tuple(str(item["evidence_id"]) for item in scoped)
        if key not in cache:
            cache[key] = build_bm25_retriever(scoped)
        return cache[key](_expanded_query(query) if expand_query else query, top_k=top_k)

    return retrieve


def build_scoped_retriever_bundle(corpus: list[dict[str, Any]], embedding, query_embedding=None):
    """Build dense/RRF once per deterministic entity-period scope."""

    cache: dict[tuple[str, ...], dict[str, Any]] = {}

    def retrieve(method: str, query: str, *, top_k: int) -> list[dict[str, Any]]:
        scoped = _scoped_corpus(query, corpus)
        if not scoped:
            return []
        key = tuple(str(item["evidence_id"]) for item in scoped)
        if key not in cache:
            bm25 = build_bm25_retriever(scoped)
            dense = build_dense_retriever(scoped, embedding, query_embedding)
            cache[key] = {
                "dense": dense,
                "hybrid_rrf": build_hybrid_rrf_retriever(scoped, bm25, dense),
            }
        return cache[key][method](query, top_k=top_k)

    return {
        "dense_entity_period_scoped": lambda query, *, top_k: retrieve("dense", query, top_k=top_k),
        "hybrid_rrf_entity_period_scoped": lambda query, *, top_k: retrieve("hybrid_rrf", query, top_k=top_k),
    }


def _model_cache_root(model_id: str) -> Path:
    return Path.home() / ".cache" / "huggingface" / "hub" / f"models--{model_id.replace('/', '--')}"


def load_local_embedding_backend(name: str):
    if name not in EMBEDDING_BACKENDS:
        raise ValueError(f"unknown embedding backend: {name}")
    config = EMBEDDING_BACKENDS[name]
    model_id = str(config["model"])
    query_instruction = str(config["query_instruction"])
    batch_size = int(config.get("batch_size", 32))
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_id, local_files_only=True)

    def encode(texts: list[str]) -> list[list[float]]:
        return model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=batch_size).tolist()

    def encode_query(texts: list[str]) -> list[list[float]]:
        prepared = [f"{query_instruction}{text}" for text in texts] if query_instruction else texts
        return encode(prepared)

    return encode, encode_query, embedding_runtime_metadata(
        model_id,
        query_instruction=query_instruction,
        dimension=int(model.get_embedding_dimension()),
        batch_size=batch_size,
    )


def embedding_runtime_metadata(model_id: str, *, query_instruction: str, dimension: int, batch_size: int = 32) -> dict[str, Any]:
    cache_root = _model_cache_root(model_id)
    ref = cache_root / "refs" / "main"
    return {
        "model": model_id,
        "model_revision": ref.read_text(encoding="utf-8").strip() if ref.is_file() else "unresolved-local-cache",
        "sentence_transformers": importlib.metadata.version("sentence-transformers"),
        "transformers": importlib.metadata.version("transformers"),
        "torch": importlib.metadata.version("torch"),
        "network_policy": "HF_HUB_OFFLINE=1",
        "query_instruction": query_instruction,
        "dimension": dimension,
        "batch_size": batch_size,
    }


def _normalise_fact_text(value: Any) -> str:
    return re.sub(r"[\s,，]", "", str(value))


def _fact_values(case: dict[str, Any]) -> list[str]:
    return [
        # `value` is the canonical answer value.  When a filing table uses a
        # scaled unit (for example 万元), `evidence_value` preserves the exact
        # number printed in the cited source for label-integrity validation.
        _normalise_fact_text(fact.get("evidence_value", fact.get("value", "")))
        for fact in case["expected"].get("answer_facts", [])
        if _normalise_fact_text(fact.get("evidence_value", fact.get("value", "")))
    ]


def _fact_suggestions(case: dict[str, Any], corpus: list[dict[str, Any]]) -> dict[str, list[str]]:
    filenames = {
        str(item.get("filename", ""))
        for item in case["expected"].get("required_citations", [])
    }
    document_ids = {filename.removesuffix(".pdf") for filename in filenames if filename}
    suggestions: dict[str, list[str]] = {}
    for value in _fact_values(case):
        suggestions[value] = [
            str(item["evidence_id"])
            for item in corpus
            if (not document_ids or str(item.get("document_id")) in document_ids)
            and value in _normalise_fact_text(item.get("content", ""))
        ][:20]
    return suggestions


def add_fact_support_diagnostics(
    metrics: dict[str, Any],
    cases: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    *,
    k: int,
) -> dict[str, Any]:
    """Measure answer-bearing retrieval separately from single-ID gold labels.

    These are diagnostics generated from answer labels, not headline Golden Set
    metrics. They reveal when a retriever finds an alternative supporting page
    that the pending human review has not yet marked as relevant.
    """

    by_case = {str(case["id"]): case for case in cases}
    by_id = {str(item["evidence_id"]): item for item in corpus}
    details: list[dict[str, Any]] = []
    for result in metrics["details"]:
        case = by_case[str(result["id"])]
        facts = case["expected"].get("answer_facts", [])
        if not facts:
            continue
        retrieved = [by_id[evidence_id] for evidence_id in result["result_ids"] if evidence_id in by_id]
        value_hits: list[bool] = []
        context_hits: list[bool] = []
        for fact in facts:
            value = _normalise_fact_text(fact.get("evidence_value", fact.get("value", "")))
            aliases = FACT_CONTEXT_ALIASES.get(str(fact.get("name", "")), ())
            value_hits.append(any(value in _normalise_fact_text(item.get("content", "")) for item in retrieved))
            context_hits.append(any(
                value in _normalise_fact_text(item.get("content", ""))
                and (not aliases or any(_normalise_fact_text(alias) in _normalise_fact_text(item.get("content", "")) for alias in aliases))
                for item in retrieved
            ))
        details.append({
            "id": case["id"],
            "fact_value_hit": all(value_hits),
            "fact_and_metric_context_hit": all(context_hits),
        })
    total = len(details)
    metrics["candidate_diagnostics"] = {
        f"fact_value_hit_at_{k}": round(sum(item["fact_value_hit"] for item in details) / total, 4) if total else 0.0,
        f"fact_and_metric_context_hit_at_{k}": round(sum(item["fact_and_metric_context_hit"] for item in details) / total, 4) if total else 0.0,
        "warning": "Answer-label-derived diagnostic; not a production or resume metric.",
        "details": details,
    }
    if any(case.get("variant_type") for case in cases):
        metric_details = {str(item["id"]): item for item in metrics["details"]}
        fact_details = {str(item["id"]): item for item in details}
        groups: dict[str, list[str]] = {}
        abstention_groups: dict[str, list[str]] = {}
        for case in cases:
            variant_type = str(case.get("variant_type") or "unclassified")
            target = abstention_groups if not case["expected"].get("answer_facts") else groups
            target.setdefault(variant_type, []).append(str(case["id"]))
        metrics["query_variant_breakdown"] = {
            variant_type: {
                "cases": len(case_ids),
                f"strict_recall_at_{k}": round(sum(metric_details[case_id]["hit"] for case_id in case_ids) / len(case_ids), 4),
                "citation_hit_rate": round(sum(metric_details[case_id]["citation_hit"] for case_id in case_ids) / len(case_ids), 4),
                f"fact_and_metric_context_hit_at_{k}": round(sum(fact_details[case_id]["fact_and_metric_context_hit"] for case_id in case_ids) / len(case_ids), 4),
            }
            for variant_type, case_ids in groups.items()
        }
        metrics["query_variant_abstention_breakdown"] = {
            variant_type: {
                "cases": len(case_ids),
                "abstain_retrieval_compliance_rate": round(
                    sum(bool(metric_details[case_id]["abstain_retrieval_ok"]) for case_id in case_ids) / len(case_ids), 4
                ),
            }
            for variant_type, case_ids in abstention_groups.items()
        }
    return metrics


def validate_label_integrity(cases: list[dict[str, Any]], corpus: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(item["evidence_id"]): item for item in corpus}
    errors: list[dict[str, Any]] = []
    for case in cases:
        expected = case["expected"]
        relevant_ids = expected.get("relevant_evidence_ids", [])
        missing_ids = [evidence_id for evidence_id in relevant_ids if evidence_id not in by_id]
        actual_citations = [
            {
                "filename": by_id[evidence_id]["filename"],
                "page": int(by_id[evidence_id]["page"]),
                "section": by_id[evidence_id]["section"],
            }
            for evidence_id in relevant_ids
            if evidence_id in by_id
        ]
        labelled_citations = expected.get("required_citations", [])
        citation_matches_index = not labelled_citations or all(
            any(citation_matches(labelled, actual) for actual in actual_citations)
            for labelled in labelled_citations
        )
        relevant_text = "\n".join(
            str(by_id[evidence_id].get("content", ""))
            for evidence_id in relevant_ids
            if evidence_id in by_id
        )
        normalised_relevant_text = _normalise_fact_text(relevant_text)
        missing_fact_values = [value for value in _fact_values(case) if value not in normalised_relevant_text]
        if missing_ids or not citation_matches_index or missing_fact_values:
            errors.append(
                {
                    "id": case["id"],
                    "missing_evidence_ids": missing_ids,
                    "missing_fact_values": missing_fact_values,
                    "citation_matches_index": citation_matches_index,
                    "indexed_citations": actual_citations,
                    "suggested_evidence_ids_by_fact": _fact_suggestions(case, corpus) if missing_fact_values else {},
                }
            )
    return {"valid": not errors, "error_count": len(errors), "errors": errors}


def run(
    cases_path: Path = DEFAULT_CASES,
    chunks_path: Path = DEFAULT_CHUNKS,
    *,
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
    k: int = 10,
    methods: tuple[str, ...] = ("bm25_global", "bm25_entity_period_scoped"),
    embedding_model: str = "project_text2vec",
    dataset_tier: str = "candidate_pending_human_review",
    claim_boundary: str = (
        "This result is a corpus-construction baseline, not a production or resume metric until every label is independently reviewed."
    ),
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    corpus = load_candidate_corpus(chunks_path, source_manifest)
    retrievers = {
        "bm25_global": build_bm25_retriever(corpus),
        "bm25_entity_period_scoped": build_scoped_bm25_retriever(corpus),
        "bm25_entity_period_scoped_alias": build_scoped_bm25_retriever(corpus, expand_query=True),
    }
    model_runtime = None
    dense_methods = {"dense_entity_period_scoped", "hybrid_rrf_entity_period_scoped"}
    if dense_methods & set(methods):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        document_embedding, query_embedding, model_runtime = load_local_embedding_backend(embedding_model)
        retrievers.update(build_scoped_retriever_bundle(corpus, document_embedding, query_embedding))
    unknown = sorted(set(methods) - set(retrievers))
    if unknown:
        raise ValueError(f"unknown methods: {', '.join(unknown)}")
    return {
        "dataset_tier": dataset_tier,
        "claim_boundary": claim_boundary,
        "cases": len(cases),
        "corpus_chunks": len(corpus),
        "embedding_runtime": model_runtime,
        "label_integrity": validate_label_integrity(cases, corpus),
        "results": {
            name: add_fact_support_diagnostics(
                evaluate_retrieval_cases(cases, retrievers[name], k=k), cases, corpus, k=k
            )
            for name in methods
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BM25 baseline for AlphaStock public-filing RAG candidates")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=(
            "bm25_global",
            "bm25_entity_period_scoped",
            "bm25_entity_period_scoped_alias",
            "dense_entity_period_scoped",
            "hybrid_rrf_entity_period_scoped",
        ),
        default=["bm25_global", "bm25_entity_period_scoped"],
    )
    parser.add_argument("--embedding-model", choices=tuple(EMBEDDING_BACKENDS), default="project_text2vec")
    parser.add_argument("--dataset-tier", default="candidate_pending_human_review")
    parser.add_argument(
        "--claim-boundary",
        default="This result is a corpus-construction baseline, not a production or resume metric until every label is independently reviewed.",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = run(
        args.cases,
        args.chunks,
        source_manifest=args.source_manifest,
        k=args.k,
        methods=tuple(args.methods),
        embedding_model=args.embedding_model,
        dataset_tier=args.dataset_tier,
        claim_boundary=args.claim_boundary,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
