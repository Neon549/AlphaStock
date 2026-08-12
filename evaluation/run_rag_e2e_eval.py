"""Run an end-to-end RAG answer evaluation.

This runner deliberately separates three measurements that are often mixed
up in RAG reports:

* retrieval_hit_rate: did retrieval return a labelled evidence page?
* answer_accuracy: did the generated answer match the benchmark answer?
* grounded_answer_accuracy: was the answer correct and did it cite retrieved,
  labelled evidence?

The generator and judge are intentionally OpenAI-compatible through the
project's configured ``quick_llm``.  The deterministic judge is useful for
numeric internal cases and tests, but it is not a substitute for a reviewed
benchmark judge on open-ended answers.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Callable

from evaluation.rag_golden_eval import citation_matches, load_cases
from evaluation.run_candidate_rag_eval import (
    build_scoped_bm25_retriever,
    load_candidate_corpus,
)
from evaluation.rag_snapshot_retrievers import build_bm25_retriever
from evaluation.rag_snapshot_retrievers import build_reranked_retriever


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "evaluation" / "corpus" / "financebench_v1" / "rag_public_gold.jsonl"
DEFAULT_CHUNKS = ROOT / "runtime" / "reports" / "financebench-v1.chunks-1200.jsonl"
DEFAULT_SOURCE_MANIFEST = ROOT / "evaluation" / "corpus" / "financebench_v1" / "sources.json"


def _response_text(response: Any) -> str:
    return str(getattr(response, "content", response)).strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    """Parse JSON returned directly or inside a markdown code fence."""

    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if fenced:
        candidates.insert(0, fenced.group(1))
    object_match = re.search(r"\{.*\}", text, flags=re.S)
    if object_match:
        candidates.append(object_match.group(0))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_generated_answer(text: str) -> dict[str, Any]:
    """Normalize a model response into answer/citations/abstention fields."""

    parsed = _extract_json(text)
    if parsed is None:
        return {"answer": text.strip(), "citations": [], "abstained": False, "parse_ok": False}
    citations = parsed.get("citations", [])
    if not isinstance(citations, list):
        citations = []
    normalized_citations = [item for item in citations if isinstance(item, dict)]
    answer = parsed.get("answer", parsed.get("final_answer", ""))
    return {
        "answer": str(answer).strip(),
        "citations": normalized_citations,
        "abstained": bool(parsed.get("abstained", False)),
        "parse_ok": True,
    }


def _case_reference(case: dict[str, Any]) -> str:
    if case.get("reference_answer") is not None:
        return str(case["reference_answer"])
    expected = case.get("expected", {})
    facts = expected.get("answer_facts", [])
    if facts:
        return "; ".join(
            f"{fact.get('name', 'fact')}={fact.get('value', '')} {fact.get('unit', '')}".strip()
            for fact in facts
        )
    return ""


def _finance_query_expansion(query: str) -> str:
    """Add deterministic filing vocabulary without using benchmark labels.

    SEC filings use stable accounting labels that differ from user wording.
    These expansions are derived from the question text itself and are part of
    the retrieval method, not answer leakage.
    """

    expansions: list[str] = []
    lower = query.lower()
    if "capital expenditure" in lower or "capex" in lower:
        expansions.append("capital expenditures capex purchases of property plant and equipment payments for property and equipment")
    if "ppne" in lower or "pp&e" in lower or "property plant" in lower:
        expansions.append("property plant and equipment property and equipment net accumulated depreciation")
    if "capital-intensive" in lower or "capital intensive" in lower:
        expansions.append("capital expenditures revenue net sales total assets property plant and equipment fixed assets net income return on assets statement of income balance sheet cash flows")
    if "cash flow" in lower:
        expansions.append("consolidated statement of cash flows cash flows from investing activities")
    if "balance sheet" in lower:
        expansions.append("consolidated balance sheet assets liabilities")
    if "statement of income" in lower or "income statement" in lower:
        expansions.append("consolidated statement of income net sales operating income")
    if "operating margin" in lower:
        expansions.append("operating income net sales gross margin SG&A")
    if "debt" in lower:
        expansions.append("total debt long-term debt current maturities borrowings")
    return " ".join([query, *expansions])


def _wrap_expanded_retriever(retriever: Callable[..., list[dict[str, Any]]]):
    return lambda query, *, top_k: retriever(_finance_query_expansion(query), top_k=top_k)


def _build_local_reranker() -> Callable[[str, list[str]], list[float]]:
    import os

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import torch
    from sentence_transformers import CrossEncoder

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = CrossEncoder("BAAI/bge-reranker-v2-m3", device=device)

    def rerank(query: str, passages: list[str]) -> list[float]:
        return [
            float(value)
            for value in model.predict(
                [(query, passage) for passage in passages],
                batch_size=16,
                show_progress_bar=False,
            )
        ]

    return rerank


def _numeric_tokens(text: str) -> list[float]:
    values: list[float] = []
    for raw in re.findall(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", text):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return values


def deterministic_judge(case: dict[str, Any], answer: str) -> dict[str, Any]:
    """Conservative judge for exact/numeric internal regression cases.

    For FinanceBench prose and multi-step reasoning this intentionally returns
    ``not_applicable`` rather than pretending keyword overlap is correctness.
    """

    expected = case.get("expected", {})
    facts = expected.get("answer_facts", [])
    if not facts:
        return {"correct": None, "not_applicable": True, "reason": "open-ended case"}
    actual_numbers = _numeric_tokens(answer)
    missing: list[str] = []
    for fact in facts:
        try:
            target = float(str(fact.get("value", "")).replace(",", ""))
        except ValueError:
            missing.append(str(fact.get("name", "fact")))
            continue
        tolerance = max(abs(target) * 0.005, 0.01)
        if not any(abs(value - target) <= tolerance for value in actual_numbers):
            # Some datasets store CNY in yuan while the answer displays CNY
            # thousand/million/billion. Accept the explicit common scales.
            scales = (1000.0, 1_000_000.0, 1_000_000_000.0)
            if not any(abs(value * scale - target) <= tolerance for value in actual_numbers for scale in scales):
                missing.append(str(fact.get("name", "fact")))
    return {
        "correct": not missing,
        "not_applicable": False,
        "reason": "missing numeric facts" if missing else "all numeric facts matched",
        "missing_facts": missing,
    }


def _configured_llm():
    from config.llm_config import quick_llm

    return quick_llm


def _generate_with_llm(query: str, contexts: list[dict[str, Any]]) -> str:
    context_text = "\n\n".join(
        f"[{item.get('filename', item.get('document_id', 'document'))} p.{item.get('page', '?')}]\n{item.get('content', item.get('text', ''))}"
        for item in contexts
    )
    prompt = f"""You are a strict financial QA RAG answerer. Answer only from the supplied evidence.
If the evidence is insufficient, set abstained to true and say so. Do not use outside knowledge.
Return JSON only with this schema:
{{"answer":"short answer","citations":[{{"filename":"...pdf","page":1}}],"abstained":false}}
Only cite pages that appear in the evidence labels below.

Question:
{query}

Evidence:
{context_text}
"""
    return _response_text(_configured_llm().invoke(prompt))


def _judge_with_llm(case: dict[str, Any], answer: str) -> dict[str, Any]:
    reference = _case_reference(case)
    prompt = f"""You are a strict evaluator for financial retrieval-augmented QA.
Judge whether the candidate answer is materially correct relative to the reference answer.
Accept equivalent wording, units, rounding, and algebraically equivalent calculations.
Require every requested component; do not give credit for a plausible but unsupported answer.
Return JSON only: {{"correct":true|false,"reason":"brief"}}.

Question: {case.get('query', '')}
Reference answer: {reference}
Candidate answer: {answer}
"""
    parsed = _extract_json(_response_text(_configured_llm().invoke(prompt)))
    if parsed is None or not isinstance(parsed.get("correct"), bool):
        raise ValueError("answer judge did not return JSON with boolean correct")
    return {"correct": parsed["correct"], "reason": str(parsed.get("reason", ""))}


def _citation_ok(case: dict[str, Any], generated: dict[str, Any], retrieved: list[dict[str, Any]]) -> tuple[bool, bool]:
    expected = case.get("expected", {})
    required = expected.get("required_citations", [])
    citations = generated.get("citations", [])
    # The public response contract exposes filename + page. Section is an
    # optional index label, so requiring it here would reject valid page-level
    # citations that the model is explicitly allowed to return.
    def page_matches(required_item: dict[str, Any], actual_item: dict[str, Any]) -> bool:
        return (
            str(required_item.get("filename", "")) == str(actual_item.get("filename", ""))
            and int(required_item.get("page") or 0) == int(actual_item.get("page") or 0)
        )

    cited_required = all(any(page_matches(item, actual) for actual in citations) for item in required) if required else True
    retrieved_pages = {
        (str(item.get("filename", item.get("document_id", ""))), int(item.get("page") or 0))
        for item in retrieved
    }
    cited_retrieved = all(
        (str(item.get("filename", "")), int(item.get("page") or 0)) in retrieved_pages
        for item in citations
    ) if citations else False
    return bool(cited_required), bool(cited_retrieved)


def evaluate_rows(
    cases: list[dict[str, Any]],
    retriever: Callable[..., list[dict[str, Any]]],
    *,
    k: int,
    generator: Callable[[str, list[dict[str, Any]]], str],
    judge: Callable[[dict[str, Any], str], dict[str, Any]],
    progress_path: Path | None = None,
    existing_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = list(existing_details or [])
    completed_ids = {str(item.get("id")) for item in details}
    for index, case in enumerate(cases, start=1):
        if str(case["id"]) in completed_ids:
            continue
        retrieved = retriever(case["query"], top_k=k)
        relevant = set(case.get("expected", {}).get("relevant_evidence_ids", []))
        retrieved_ids = [str(item.get("page_evidence_id") or item.get("evidence_id")) for item in retrieved]
        retrieval_hit = bool(relevant.intersection(retrieved_ids)) if relevant else not retrieved
        try:
            raw_output = generator(case["query"], retrieved)
            generated = parse_generated_answer(raw_output)
            judge_result = judge(case, generated["answer"])
            error = None
        except Exception as exc:  # Keep a long benchmark run resumable.
            generated = {"answer": "", "citations": [], "abstained": False, "parse_ok": False}
            judge_result = {"correct": None, "error": str(exc)}
            error = str(exc)
        answer_correct = judge_result.get("correct")
        citation_ok, cited_retrieved = _citation_ok(case, generated, retrieved)
        grounded = bool(answer_correct is True and citation_ok and cited_retrieved)
        detail = {
            "id": case["id"],
            "retrieval_hit": retrieval_hit,
            "retrieved_ids": retrieved_ids,
            "generated": generated,
            "judge": judge_result,
            "citation_ok": citation_ok,
            "cited_retrieved_evidence": cited_retrieved,
            "grounded_answer_correct": grounded,
        }
        if error:
            detail["error"] = error
        details.append(detail)
        print(f"[{index}/{len(cases)}] {case['id']} answer_correct={answer_correct}", flush=True)
        if progress_path:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_text(json.dumps({"cases": len(cases), "details": details}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    judged = [item for item in details if item["judge"].get("correct") is not None]
    correct = [item for item in judged if item["judge"].get("correct") is True]
    incorrect = [item for item in judged if item["judge"].get("correct") is False]
    unjudged = [item for item in details if item["judge"].get("correct") is None]
    return {
        "cases": len(details),
        "judged_cases": len(judged),
        "correct_cases": len(correct),
        "incorrect_cases": len(incorrect),
        "unjudged_cases": len(unjudged),
        "retrieval_hit_rate_at_k": round(sum(item["retrieval_hit"] for item in details) / len(details), 4) if details else 0.0,
        "answer_accuracy": round(len(correct) / len(judged), 4) if judged else None,
        "answer_accuracy_all_cases_lower_bound": round(len(correct) / len(details), 4) if details else None,
        "citation_accuracy": round(sum(item["citation_ok"] for item in judged) / len(judged), 4) if judged else None,
        "retrieved_citation_rate": round(sum(item["cited_retrieved_evidence"] for item in judged) / len(judged), 4) if judged else None,
        "grounded_answer_accuracy": round(sum(item["grounded_answer_correct"] for item in judged) / len(judged), 4) if judged else None,
        "details": details,
    }


def run(
    cases_path: Path = DEFAULT_CASES,
    chunks_path: Path = DEFAULT_CHUNKS,
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
    *,
    k: int = 10,
    limit: int | None = None,
    retriever_name: str = "bm25_entity_period_scoped",
    generator_name: str = "configured_llm",
    judge_name: str = "configured_llm",
    progress_path: Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    if limit:
        cases = cases[:limit]
    corpus = load_candidate_corpus(chunks_path, source_manifest)
    base_name = retriever_name.removesuffix("_reranked")
    base_retriever = build_bm25_retriever(corpus) if base_name == "bm25_global" else build_scoped_bm25_retriever(corpus)
    retriever = _wrap_expanded_retriever(base_retriever)
    if retriever_name.endswith("_reranked"):
        retriever = build_reranked_retriever(retriever, _build_local_reranker(), candidate_k=100)
    generator = _generate_with_llm if generator_name == "configured_llm" else (lambda query, contexts: json.dumps({"answer": contexts[0]["content"] if contexts else "", "citations": [], "abstained": not bool(contexts)}))
    judge = _judge_with_llm if judge_name == "configured_llm" else deterministic_judge
    existing_details = None
    if resume and progress_path and progress_path.exists():
        checkpoint = json.loads(progress_path.read_text(encoding="utf-8"))
        existing_details = checkpoint.get("details", [])
    report = evaluate_rows(
        cases, retriever, k=k, generator=generator, judge=judge,
        progress_path=progress_path, existing_details=existing_details,
    )
    report.update({
        "dataset_id": "financebench-open-source-v1" if "financebench" in str(cases_path).lower() else cases_path.stem,
        "dataset_tier": "external_gold" if "financebench" in str(cases_path).lower() else "candidate_or_regression",
        "cases_path": str(cases_path),
        "corpus_chunks": len(corpus),
        "k": k,
        "retriever": retriever_name,
        "generator": generator_name,
        "judge": judge_name,
        "claim_boundary": "answer_accuracy is benchmark-judge accuracy; grounded_answer_accuracy additionally requires correct retrieved citations.",
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run end-to-end RAG answer accuracy evaluation")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retriever", choices=("bm25_global", "bm25_entity_period_scoped", "bm25_global_reranked", "bm25_entity_period_scoped_reranked"), default="bm25_entity_period_scoped_reranked")
    parser.add_argument("--generator", choices=("configured_llm", "extractive"), default="configured_llm")
    parser.add_argument("--judge", choices=("configured_llm", "deterministic"), default="configured_llm")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--progress", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    report = run(
        args.cases, args.chunks, args.source_manifest,
        k=args.k, limit=args.limit, retriever_name=args.retriever,
        generator_name=args.generator, judge_name=args.judge,
        progress_path=args.progress,
        resume=args.resume,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("dataset_id", "cases", "judged_cases", "answer_accuracy", "grounded_answer_accuracy", "retrieval_hit_rate_at_k", "out") if key in report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
