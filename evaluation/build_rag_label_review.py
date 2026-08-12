"""Build a human-review queue for alternative evidence in candidate RAG labels."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

import jieba

from evaluation.rag_golden_eval import load_cases
from evaluation.run_candidate_rag_eval import (
    DEFAULT_CASES,
    DEFAULT_CHUNKS,
    FACT_CONTEXT_ALIASES,
    _normalise_fact_text,
    add_fact_support_diagnostics,
    load_candidate_corpus,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ABLATION = ROOT / "runtime" / "reports" / "public-filings-candidate-v1.retrieval-ablation.json"


def _fact_context_candidates(case: dict[str, Any], corpus: list[dict[str, Any]]) -> list[str]:
    citations = case["expected"].get("required_citations", [])
    document_ids = {
        str(citation.get("filename", "")).removesuffix(".pdf")
        for citation in citations
        if citation.get("filename")
    }
    candidates: list[str] = []
    for item in corpus:
        if document_ids and str(item.get("document_id")) not in document_ids:
            continue
        text = _normalise_fact_text(item.get("content", ""))
        supports_all = True
        for fact in case["expected"].get("answer_facts", []):
            value = _normalise_fact_text(fact.get("value", ""))
            aliases = FACT_CONTEXT_ALIASES.get(str(fact.get("name", "")), ())
            if value not in text or (aliases and not any(_normalise_fact_text(alias) in text for alias in aliases)):
                supports_all = False
                break
        if supports_all:
            candidates.append(str(item["evidence_id"]))
    return candidates


def _answer_terms(answer: str) -> list[str]:
    """Extract stable lexical anchors for a reference-answer audit.

    This is deliberately a review aid, not a replacement for a human Gold
    label.  Numeric values receive extra weight because they are useful for
    finding a page whose citation metadata is shifted or stale.
    """

    numeric_terms = re.findall(r"\d[\d,]*(?:\.\d+)?", answer)
    text_without_numbers = re.sub(r"\d[\d,]*(?:\.\d+)?", " ", answer)
    terms: list[str] = list(numeric_terms)
    for token in jieba.lcut(text_without_numbers):
        token = token.strip()
        if len(token) >= 2 or re.search(r"\d", token):
            terms.append(token)
    return list(dict.fromkeys(terms))


def _reference_answer_candidates(case: dict[str, Any], corpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find evidence pages that lexically support an external reference answer."""

    answer = str(case.get("reference_answer") or "").strip()
    if not answer:
        return []
    citations = case["expected"].get("required_citations", [])
    document_ids = {
        str(citation.get("filename", "")).removesuffix(".pdf")
        for citation in citations
        if citation.get("filename")
    }
    terms = _answer_terms(answer)
    candidates: list[dict[str, Any]] = []
    for item in corpus:
        if document_ids and str(item.get("document_id")) not in document_ids:
            continue
        text = _normalise_fact_text(item.get("content", ""))
        matched_terms = [term for term in terms if _normalise_fact_text(term) in text]
        if not matched_terms:
            continue
        score = sum(4 if re.search(r"\d", term) else 1 for term in matched_terms)
        candidates.append({
            "evidence_id": str(item["evidence_id"]),
            "support_score": score,
            "matched_terms": matched_terms,
        })
    candidates.sort(key=lambda item: (-int(item["support_score"]), str(item["evidence_id"])))
    return candidates[:20]


def build_review_queue(
    cases: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    ablation: dict[str, Any],
) -> dict[str, Any]:
    by_evidence = {str(item["evidence_id"]): item for item in corpus}
    metric_summary: dict[str, Any] = {}
    for method, result in ablation["results"].items():
        enriched = add_fact_support_diagnostics(copy.deepcopy(result), cases, corpus, k=10)
        metric_summary[method] = {
            key: enriched[key]
            for key in (
                "recall_at_10",
                "mrr",
                "ndcg_at_10",
                "citation_hit_rate",
                "abstain_retrieval_compliance_rate",
                "candidate_diagnostics",
            )
        }
    result_details = {
        method: {str(detail["id"]): detail for detail in result["details"]}
        for method, result in ablation["results"].items()
    }
    items: list[dict[str, Any]] = []
    for case in cases:
        answer_facts = case["expected"].get("answer_facts", [])
        reference_answer = str(case.get("reference_answer") or "").strip()
        if not answer_facts and not reference_answer:
            continue
        candidate_records = (
            [{"evidence_id": evidence_id} for evidence_id in _fact_context_candidates(case, corpus)]
            if answer_facts
            else _reference_answer_candidates(case, corpus)
        )
        evidence = [{
            "evidence_id": record["evidence_id"],
            "page": by_evidence[record["evidence_id"]]["page"],
            "section": by_evidence[record["evidence_id"]]["section"],
            "preview": str(by_evidence[record["evidence_id"]]["content"])[:240],
            **({key: record[key] for key in ("support_score", "matched_terms") if key in record}),
        } for record in candidate_records if record["evidence_id"] in by_evidence]
        candidates = {record["evidence_id"] for record in candidate_records}
        method_hits = {
            method: [
                evidence_id
                for evidence_id in details[case["id"]]["result_ids"]
                if evidence_id in candidates
            ]
            for method, details in result_details.items()
        }
        items.append({
            "case_id": case["id"],
            "query": case["query"],
            "answer_facts": answer_facts,
            "reference_answer": reference_answer or None,
            "currently_labelled_evidence_ids": case["expected"]["relevant_evidence_ids"],
            "candidate_supporting_evidence": evidence,
            "retrieved_support_by_method": method_hits,
            "review_decision": "pending_human_review",
            "review_note": (
                "Confirm that each candidate states the requested period and metric, then copy approved IDs into relevant_evidence_ids."
                if answer_facts
                else "Reference-answer lexical matches are audit suggestions only; verify the source page and answer semantics before approval."
            ),
        })
    return {
        "dataset_tier": "candidate_pending_human_review",
        "source_ablation": str(DEFAULT_ABLATION.relative_to(ROOT)).replace("\\", "/"),
        "cases_for_review": len(items),
        "metric_summary": metric_summary,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build alternative-evidence review queue")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "sources.json",
        help="Source manifest matching the chunk corpus; required for non-default corpora.",
    )
    parser.add_argument("--ablation", type=Path, default=DEFAULT_ABLATION)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build_review_queue(
        load_cases(args.cases),
        load_candidate_corpus(args.chunks, args.source_manifest),
        json.loads(args.ablation.read_text(encoding="utf-8")),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases_for_review": report["cases_for_review"], "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
