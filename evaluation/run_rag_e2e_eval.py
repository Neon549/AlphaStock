"""Run an end-to-end RAG answer evaluation.

This runner deliberately separates three measurements that are often mixed
up in RAG reports:

* retrieval_hit_rate: did retrieval return a labelled evidence page?
* answer_accuracy: did the generated answer match the benchmark answer?
* grounded_answer_accuracy: was the answer correct and did it cite retrieved,
  labelled evidence?

The ``evidence_pack`` generator is a diagnostic baseline.  It concatenates
the retrieved passages and cites their pages so that evidence coverage can be
measured separately from answer synthesis.  It must not be reported as model
answer quality.

The generator and judge are intentionally OpenAI-compatible through the
project's configured ``quick_llm``.  The deterministic judge is useful for
numeric internal cases and tests, but it is not a substitute for a reviewed
benchmark judge on open-ended answers.
"""

from __future__ import annotations

import argparse
import ast
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
    # A hyphen between two digits is normally a range separator in annual
    # report tables (for example ``45.00-49.5``), not a negative sign.  Keep
    # a leading/sign-separated minus as negative, but make table ranges
    # comparable as two positive numbers.
    text = re.sub(r"(?<=\d)-(?=\d)", " ", text)
    values: list[float] = []
    for raw in re.findall(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", text):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            pass
    return values


def _unit_aliases(unit: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+", "", str(unit)).lower()
    aliases: dict[str, tuple[str, ...]] = {
        "%": ("%", "百分比", "百分之", "percent"),
        "cny": ("cny", "人民币", "元", "yuan"),
        "元": ("元", "cny", "人民币", "yuan"),
        "billioncny": ("亿元", "十亿元", "billioncny", "billion"),
        "cnythousand": ("万元", "千元", "cnythousand", "thousandcny"),
        "万吨": ("万吨", "万 吨", "tenthousandton"),
        "人": ("人", "名", "person", "people"),
        "项": ("项", "件", "个", "item"),
        "采购量": ("采购量", "支", "件", "quantity"),
    }
    return aliases.get(normalized, (str(unit),))


def _unit_matches(text: str, unit: str) -> bool:
    normalized_text = re.sub(r"\s+", "", str(text)).lower()
    normalized_unit = re.sub(r"\s+", "", str(unit)).lower()
    if normalized_unit in {"元", "cny"}:
        # Do not treat 万元/亿元 as plain 元: unit mistakes are exactly what
        # the financial answer check is intended to catch.
        return bool(re.search(r"(?<![万亿千])元", normalized_text) or "cny" in normalized_text or "yuan" in normalized_text)
    return any(alias.lower().replace(" ", "") in normalized_text for alias in _unit_aliases(unit))


def _value_unit_matches(text: str, target: float, unit: str) -> bool:
    """Check a monetary value together with a printed unit when available."""

    text = re.sub(r"(?<=\d)-(?=\d)", " ", text)
    normalized_unit = re.sub(r"\s+", "", str(unit)).lower()
    if normalized_unit not in {"元", "cny"}:
        return _number_matches(text, target) and _unit_matches(text, unit)
    pairs = re.findall(r"([-+]?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|千元|元|cny)", text.lower())
    factors = {"亿元": 100_000_000.0, "万元": 10_000.0, "千元": 1_000.0, "元": 1.0, "cny": 1.0}
    if pairs:
        tolerance = max(abs(target) * 0.005, 0.01)
        return any(
            abs(float(raw.replace(",", "")) * factors[suffix] - target) <= tolerance
            for raw, suffix in pairs
        )
    return _number_matches(text, target) and _unit_matches(text, unit)


def _number_matches(text: str, target: float) -> bool:
    actual_numbers = _numeric_tokens(text)
    tolerance = max(abs(target) * 0.005, 0.01)
    if any(abs(value - target) <= tolerance for value in actual_numbers):
        return True
    # Financial filings commonly print values in 元/万元/亿元 while the
    # benchmark stores one canonical unit.  Accept only explicit decimal
    # scale conversions, not arbitrary fuzzy matches.
    scales = (1000.0, 10_000.0, 100_000_000.0, 1_000_000.0, 1_000_000_000.0)
    return any(abs(value * scale - target) <= tolerance for value in actual_numbers for scale in scales)


def _fact_supported_by_text(fact: dict[str, Any], text: str, *, require_unit: bool = True) -> bool:
    try:
        target = float(str(fact.get("value", "")).replace(",", ""))
    except ValueError:
        return False
    if not _number_matches(text, target):
        return False
    unit = str(fact.get("unit", "")).strip()
    return not unit or not require_unit or _value_unit_matches(text, target, unit)


def _calculation_supported_by_retrieved(case: dict[str, Any], retrieved: list[dict[str, Any]]) -> bool | None:
    calculation = case.get("expected", {}).get("calculation")
    if not calculation:
        return None
    text = "\n".join(str(item.get("content") or item.get("text") or "") for item in retrieved)
    operands = [calculation.get("numerator"), calculation.get("denominator")]
    operands = [item for item in operands if isinstance(item, dict)]
    if not operands:
        # For formulas without explicit operand metadata, use the literal
        # numbers in the formula, excluding the expected result.
        operands = [{"value": value} for value in _numeric_tokens(str(calculation.get("formula", "")))]
    operands_supported = all(
        _number_matches(text, float(str(item.get("value", "")).replace(",", "")))
        for item in operands
    )
    try:
        expected_value = float(str(calculation.get("expected_value", "")).replace(",", ""))
    except ValueError:
        return False
    try:
        tree = ast.parse(str(calculation.get("formula", "")), mode="eval")

        def calculate(node: ast.AST) -> float:
            if isinstance(node, ast.Expression):
                return calculate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = calculate(node.operand)
                return value if isinstance(node.op, ast.UAdd) else -value
            if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                left, right = calculate(node.left), calculate(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                return left / right
            raise ValueError("unsupported calculation expression")

        calculated_value = calculate(tree)
    except (SyntaxError, ValueError, ZeroDivisionError):
        return False
    tolerance = max(abs(expected_value) * 0.005, 0.01)
    return bool(operands_supported and abs(calculated_value - expected_value) <= tolerance)


def _retrieved_fact_support(case: dict[str, Any], retrieved: list[dict[str, Any]]) -> bool | None:
    facts = case.get("expected", {}).get("answer_facts", [])
    if not facts:
        return None
    calculation_name = str(case.get("expected", {}).get("calculation", {}).get("name", ""))
    direct_facts = [fact for fact in facts if str(fact.get("name", "")) != calculation_name]
    if not direct_facts:
        return None
    text = "\n".join(str(item.get("content") or item.get("text") or "") for item in retrieved)
    # A table may print the unit once in a header or omit it in the cell. The
    # value-coverage diagnostic therefore checks values only; answer judging
    # still checks units in the generated response.
    return all(_fact_supported_by_text(fact, text, require_unit=False) for fact in direct_facts)


def deterministic_judge(case: dict[str, Any], answer: str) -> dict[str, Any]:
    """Conservative judge for exact/numeric internal regression cases.

    For FinanceBench prose and multi-step reasoning this intentionally returns
    ``not_applicable`` rather than pretending keyword overlap is correctness.
    """

    expected = case.get("expected", {})
    facts = expected.get("answer_facts", [])
    if not facts:
        return {"correct": None, "not_applicable": True, "reason": "open-ended case"}
    missing: list[str] = []
    missing_units: list[str] = []
    for fact in facts:
        try:
            target = float(str(fact.get("value", "")).replace(",", ""))
        except ValueError:
            missing.append(str(fact.get("name", "fact")))
            continue
        if not _number_matches(answer, target):
            missing.append(str(fact.get("name", "fact")))
        elif fact.get("unit") and not _value_unit_matches(answer, target, str(fact["unit"])):
            missing_units.append(str(fact.get("name", "fact")))
    return {
        "correct": not missing and not missing_units,
        "not_applicable": False,
        "reason": (
            "missing numeric facts" if missing else
            "missing or inconsistent units" if missing_units else
            "all numeric facts and units matched"
        ),
        "missing_facts": missing,
        "missing_units": missing_units,
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


def _generate_evidence_pack(query: str, contexts: list[dict[str, Any]]) -> str:
    """Return all retrieved evidence as a diagnostic, not a model answer.

    This mode answers a narrow question: if the retriever returned the top-k
    passages, do those passages contain enough labelled evidence for the
    deterministic fact judge?  Keeping it separate from ``configured_llm``
    prevents retrieval coverage from being confused with generation quality.
    """

    del query
    citations: list[dict[str, Any]] = []
    passages: list[str] = []
    seen_pages: set[tuple[str, int]] = set()
    for item in contexts:
        filename = str(item.get("filename", item.get("document_id", "document")))
        page = int(item.get("page") or 0)
        page_key = (filename, page)
        if page_key not in seen_pages:
            citations.append({"filename": filename, "page": page})
            seen_pages.add(page_key)
        passages.append(
            f"[{filename} p.{page}]\n{item.get('content', item.get('text', ''))}"
        )
    return json.dumps(
        {
            "answer": "\n\n".join(passages),
            "citations": citations,
            "abstained": not bool(contexts),
            "diagnostic_only": True,
        },
        ensure_ascii=False,
    )


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
        fact_support = _retrieved_fact_support(case, retrieved)
        calculation_support = _calculation_supported_by_retrieved(case, retrieved)
        detail = {
            "id": case["id"],
            "retrieval_hit": retrieval_hit,
            "retrieved_ids": retrieved_ids,
            "generated": generated,
            "judge": judge_result,
            "citation_ok": citation_ok,
            "cited_retrieved_evidence": cited_retrieved,
            "grounded_answer_correct": grounded,
            "evidence_support": {
                "direct_fact_value_support": fact_support,
                "calculation_operand_support": calculation_support,
            },
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
    fact_support_cases = [item for item in details if item["evidence_support"]["direct_fact_value_support"] is not None]
    calculation_cases = [item for item in details if item["evidence_support"]["calculation_operand_support"] is not None]
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
        "direct_fact_value_support_cases": len(fact_support_cases),
        "direct_fact_value_support_rate": round(sum(bool(item["evidence_support"]["direct_fact_value_support"]) for item in fact_support_cases) / len(fact_support_cases), 4) if fact_support_cases else None,
        "calculation_operand_support_cases": len(calculation_cases),
        "calculation_operand_support_rate": round(sum(bool(item["evidence_support"]["calculation_operand_support"]) for item in calculation_cases) / len(calculation_cases), 4) if calculation_cases else None,
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
    if generator_name == "configured_llm":
        generator = _generate_with_llm
    elif generator_name == "evidence_pack":
        generator = _generate_evidence_pack
    else:
        generator = lambda query, contexts: json.dumps({
            "answer": contexts[0]["content"] if contexts else "",
            "citations": [],
            "abstained": not bool(contexts),
        })
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
    parser.add_argument("--generator", choices=("configured_llm", "extractive", "evidence_pack"), default="configured_llm")
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
    print(json.dumps({key: report[key] for key in (
        "dataset_id", "cases", "judged_cases", "answer_accuracy",
        "grounded_answer_accuracy", "retrieval_hit_rate_at_k",
        "direct_fact_value_support_rate", "calculation_operand_support_rate", "out",
    ) if key in report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
