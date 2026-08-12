"""Run a deterministic, non-Gold audit over RAG candidate labels.

This is a sidecar to the normal RAG evaluation.  It never edits candidate
cases and never changes Recall, MRR, NDCG, citation-hit, or abstention metrics.
It only reports whether the current label is mechanically supported, whether a
better evidence page can be suggested, and which cases still need review.

The output is intentionally not a human-reviewed dataset.  ``auto_accept``
means that deterministic checks passed; it does not create production-tier
provenance.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from evaluation.build_rag_label_review import _answer_terms
from evaluation.rag_golden_eval import citation_matches, load_cases
from evaluation.run_candidate_rag_eval import (
    DEFAULT_CASES,
    DEFAULT_CHUNKS,
    FACT_CONTEXT_ALIASES,
    _normalise_fact_text,
    load_candidate_corpus,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ABLATION = ROOT / "runtime" / "reports" / "public-filings-candidate-v1.retrieval-ablation.json"
DEFAULT_SOURCE_MANIFEST = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "sources.json"


def _document_ids(case: dict[str, Any]) -> set[str]:
    filenames = {
        str(citation.get("filename", ""))
        for citation in case.get("expected", {}).get("required_citations", [])
        if citation.get("filename")
    }
    ids = {filename.removesuffix(".pdf") for filename in filenames}
    if ids:
        return ids
    return {
        str(evidence_id).rsplit(":p", 1)[0]
        for evidence_id in case.get("expected", {}).get("relevant_evidence_ids", [])
        if ":p" in str(evidence_id)
    }


def _citation(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": str(item.get("filename") or f"{item.get('document_id', '')}.pdf"),
        "page": int(item.get("page") or 0),
        "section": str(item.get("section") or " / ".join(item.get("parent_path", []))),
    }


def _fact_match(item: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    text = _normalise_fact_text(item.get("content", item.get("text", "")))
    value = _normalise_fact_text(fact.get("evidence_value", fact.get("value", "")))
    aliases = FACT_CONTEXT_ALIASES.get(str(fact.get("name", "")), ())
    matched_aliases = [alias for alias in aliases if _normalise_fact_text(alias) in text]
    return {
        "name": str(fact.get("name", "")),
        "value": str(fact.get("evidence_value", fact.get("value", ""))),
        "value_match": bool(value and value in text),
        "metric_context_match": not aliases or bool(matched_aliases),
        "matched_aliases": matched_aliases,
    }


def _structured_candidates(case: dict[str, Any], corpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    facts = case.get("expected", {}).get("answer_facts", [])
    document_ids = _document_ids(case)
    candidates: list[dict[str, Any]] = []
    for item in corpus:
        if document_ids and str(item.get("document_id")) not in document_ids:
            continue
        matches = [_fact_match(item, fact) for fact in facts]
        if not matches or not all(match["value_match"] for match in matches):
            continue
        context_match = all(match["metric_context_match"] for match in matches)
        score = sum(4 if match["value_match"] else 0 for match in matches)
        score += sum(2 if match["metric_context_match"] else 0 for match in matches)
        candidates.append({
            "evidence_id": str(item["evidence_id"]),
            "page": int(item.get("page") or 0),
            "section": str(item.get("section", "")),
            "score": score,
            "all_values_match": True,
            "all_metric_context_match": context_match,
            "fact_matches": matches,
            "preview": str(item.get("content", item.get("text", "")))[:240],
        })
    candidates.sort(key=lambda item: (-int(item["all_metric_context_match"]), -int(item["score"]), item["evidence_id"]))
    return candidates[:20]


def _reference_candidates(case: dict[str, Any], corpus: list[dict[str, Any]]) -> list[dict[str, Any]]:
    answer = str(case.get("reference_answer") or "").strip()
    if not answer:
        return []
    terms = _answer_terms(answer)
    numeric_terms = [term for term in terms if re.search(r"\d", term)]
    document_ids = _document_ids(case)
    candidates: list[dict[str, Any]] = []
    for item in corpus:
        if document_ids and str(item.get("document_id")) not in document_ids:
            continue
        text = _normalise_fact_text(item.get("content", item.get("text", "")))
        matched_terms = [term for term in terms if _normalise_fact_text(term) in text]
        matched_numeric = [term for term in numeric_terms if _normalise_fact_text(term) in text]
        if not matched_terms:
            continue
        score = sum(4 if re.search(r"\d", term) else 1 for term in matched_terms)
        candidates.append({
            "evidence_id": str(item["evidence_id"]),
            "page": int(item.get("page") or 0),
            "section": str(item.get("section", "")),
            "score": score,
            "matched_terms": matched_terms,
            "matched_numeric_terms": matched_numeric,
            "numeric_terms_total": len(numeric_terms),
            "preview": str(item.get("content", item.get("text", "")))[:240],
        })
    candidates.sort(key=lambda item: (-len(item["matched_numeric_terms"]), -int(item["score"]), item["evidence_id"]))
    return candidates[:20]


def _current_label_checks(case: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    expected = case.get("expected", {})
    current_ids = [str(value) for value in expected.get("relevant_evidence_ids", [])]
    current_items = [by_id[value] for value in current_ids if value in by_id]
    facts = expected.get("answer_facts", [])
    fact_matches = [_fact_match(item, fact) for item in current_items for fact in facts]
    citation_checks = [
        any(citation_matches(required, _citation(item)) for item in current_items)
        for required in expected.get("required_citations", [])
    ]
    return {
        "evidence_ids_present": len(current_items) == len(current_ids),
        "fact_values_supported": bool(facts) and all(match["value_match"] for match in fact_matches),
        "metric_context_supported": bool(facts) and all(match["metric_context_match"] for match in fact_matches),
        "required_citations_match_index": not citation_checks or all(citation_checks),
        "current_evidence_ids": current_ids,
    }


def _ablation_details(ablation: dict[str, Any] | None) -> tuple[str | None, dict[str, dict[str, Any]]]:
    if not ablation or not ablation.get("results"):
        return None, {}
    preferred = (
        "bm25_entity_period_scoped_alias",
        "bm25_entity_period_scoped",
        "bm25_global",
    )
    method = next((name for name in preferred if name in ablation["results"]), next(iter(ablation["results"])))
    details = {str(item["id"]): item for item in ablation["results"][method].get("details", [])}
    return method, details


def audit_cases(
    cases: list[dict[str, Any]],
    corpus: list[dict[str, Any]],
    *,
    ablation: dict[str, Any] | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Produce a sidecar audit without changing the input cases or metrics."""

    by_id = {str(item["evidence_id"]): item for item in corpus}
    retrieval_method, retrieval_details = _ablation_details(ablation)
    items: list[dict[str, Any]] = []
    for case in cases:
        expected = case.get("expected", {})
        current = _current_label_checks(case, by_id)
        facts = expected.get("answer_facts", [])
        reference_answer = str(case.get("reference_answer") or "").strip()
        candidates = _structured_candidates(case, corpus) if facts else _reference_candidates(case, corpus)
        current_ids = set(current["current_evidence_ids"])
        current_candidates = [candidate for candidate in candidates if candidate["evidence_id"] in current_ids]
        detail = retrieval_details.get(str(case["id"]), {})
        result_ids = [str(value) for value in detail.get("result_ids", [])[:top_k]]

        reasons: list[str] = []
        status = "needs_review"
        confidence = "low"
        recommended = candidates[0] if candidates else None
        if not facts and not reference_answer:
            status = "auto_accept_abstention" if detail.get("abstain_retrieval_ok") is True else "needs_review"
            confidence = "high" if status != "needs_review" else "low"
            reasons.append("abstention label has no answer facts or reference answer")
            if detail.get("abstain_retrieval_ok") is True:
                reasons.append(f"{retrieval_method or 'selected retriever'} returned no evidence")
        elif facts:
            current_verified = (
                current["evidence_ids_present"]
                and current["fact_values_supported"]
                and current["metric_context_supported"]
                and current["required_citations_match_index"]
            )
            if current_verified:
                status = "auto_accept_current"
                confidence = "high"
                reasons.append("current evidence contains every fact value and metric context")
                reasons.append("current evidence IDs match required file/page citations")
            elif recommended and recommended["all_metric_context_match"]:
                status = "auto_repair_candidate"
                confidence = "medium"
                reasons.append("current label is not fully supported, but an evidence chunk contains all fact values and metric context")
                if current_candidates:
                    reasons.append("the suggested evidence differs from or conflicts with the current label")
            else:
                reasons.append("no single candidate chunk passed the exact value and metric-context checks")
        else:
            numeric_total = int(recommended["numeric_terms_total"]) if recommended else 0
            numeric_hit = len(recommended["matched_numeric_terms"]) if recommended else 0
            current_numeric = max((len(candidate["matched_numeric_terms"]) for candidate in current_candidates), default=0)
            if recommended and numeric_total and numeric_hit == numeric_total and len(candidates) == 1:
                status = "auto_repair_candidate"
                confidence = "medium"
                reasons.append("reference answer numeric anchors uniquely identify one page")
            elif current_candidates and numeric_total and current_numeric == numeric_total:
                status = "auto_accept_current"
                confidence = "medium"
                reasons.append("current page contains all numeric anchors from the reference answer")
            else:
                reasons.append("reference-answer lexical matching is insufficient for an automatic Gold label")

        if status == "auto_accept_current":
            recommended_evidence_ids = list(current["current_evidence_ids"])
        elif status == "auto_accept_abstention":
            recommended_evidence_ids = []
        else:
            recommended_evidence_ids = [recommended["evidence_id"]] if recommended else []
        recommended_item = by_id.get(recommended_evidence_ids[0]) if recommended_evidence_ids else None
        items.append({
            "case_id": str(case["id"]),
            "query": str(case.get("query", "")),
            "status": status,
            "confidence": confidence,
            "reasons": reasons,
            "current_label_checks": current,
            "recommended_evidence_ids": recommended_evidence_ids,
            "recommended_citation": _citation(recommended_item) if recommended_item else None,
            "candidate_supporting_evidence": candidates,
            "retrieval_check": {
                "method": retrieval_method,
                "top_k": top_k,
                "result_ids": result_ids,
                "current_label_hit": bool(current_ids.intersection(result_ids)),
                "retrieved_abstain_ok": detail.get("abstain_retrieval_ok"),
            },
            "auto_review_provenance": {
                "method": "deterministic_exact_fact_metric_citation_and_lexical_checks",
                "human_reviewer": None,
                "human_reviewed_at": None,
                "warning": "Automatic audit only; do not promote this row to production Gold provenance.",
            },
        })

    counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
        confidence_counts[item["confidence"]] = confidence_counts.get(item["confidence"], 0) + 1
    return {
        "schema_version": "rag-auto-review-v1",
        "dataset_tier": "candidate_auto_checked",
        "claim_boundary": (
            "Deterministic sidecar audit only. It does not change retrieval metrics and is not an independent human-reviewed or production test set."
        ),
        "cases": len(items),
        "summary": {"by_status": counts, "by_confidence": confidence_counts},
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a sidecar deterministic audit over RAG candidate labels")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--ablation", type=Path)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    ablation = json.loads(args.ablation.read_text(encoding="utf-8")) if args.ablation else None
    report = audit_cases(
        load_cases(args.cases),
        load_candidate_corpus(args.chunks, args.source_manifest),
        ablation=ablation,
        top_k=args.top_k,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": report["cases"], "summary": report["summary"], "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
