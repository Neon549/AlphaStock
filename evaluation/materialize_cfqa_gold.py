"""Materialize page-anchored CFQA rows into AlphaStock's RAG case schema."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _score(answer: str, text: str) -> int:
    answer = _compact(answer)
    text = _compact(text)
    score = 0
    for size in (12, 8, 6, 4):
        for start in range(0, max(0, len(answer) - size + 1), max(1, size // 2)):
            phrase = answer[start : start + size]
            if len(phrase) == size and phrase in text:
                score += size
    numbers = re.findall(r"\d[\d,.%-]*", answer)
    score += sum(5 for number in numbers if number in text)
    return score


def _pages(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        result: list[int] = []
        for item in value:
            result.extend(_pages(item))
        return sorted(set(result))
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--corpus-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--unresolved-output", type=Path)
    args = parser.parse_args()

    candidates = [json.loads(line) for line in args.candidates.read_text(encoding="utf-8").splitlines() if line.strip()]
    chunks = [json.loads(line) for line in args.chunks.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_document_page: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for chunk in chunks:
        by_document_page.setdefault((str(chunk["document_id"]), int(chunk["page"])), []).append(chunk)

    output: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for candidate in candidates:
        code = str(candidate["stock_code"])
        year = re.search(r"20\d{2}", str(candidate["query"]))
        document_rows = list({str(chunk["document_id"]): chunk for chunk in chunks}.values())
        matching_documents = [document for document in document_rows if str(document.get("security_code")) == code]
        if year:
            year_matches = [document for document in matching_documents if str(year.group()) in str(document.get("report_period"))]
            if year_matches:
                matching_documents = year_matches
        if len(matching_documents) != 1:
            unresolved.append({
                **candidate,
                "status": "source_resolution_pending",
                "resolution_error": {
                    "security_code": code,
                    "query_year": year.group() if year else None,
                    "matching_documents": [document.get("document_id") for document in matching_documents],
                },
            })
            continue
        document_id = str(matching_documents[0]["document_id"])
        pages = _pages(candidate["answer_pdf_pages"])
        selected: list[dict[str, Any]] = []
        for page in pages:
            page_chunks = by_document_page.get((document_id, page), [])
            if not page_chunks:
                raise SystemExit(f"{candidate['id']}: page {page} missing from indexed corpus")
            selected.append(max(page_chunks, key=lambda chunk: _score(candidate["reference_answer"], chunk["text"])))
        output.append(
            {
                "id": candidate["id"],
                "split": "validation",
                "source_type": "annual_report",
                "corpus_version": args.corpus_version,
                "query": candidate["query"],
                "expected": {
                    "answer_facts": [],
                    "relevant_evidence_ids": [str(chunk["evidence_id"]) for chunk in selected],
                    "required_citations": [
                        {"filename": f"{document_id}.pdf", "page": page, "section": " / ".join(selected[index].get("parent_path", []))}
                        for index, page in enumerate(pages)
                    ],
                    "abstain_allowed": False,
                },
                "reference_answer": candidate["reference_answer"],
                "tags": ["cfqa", "public_real_investor_qa", "page_anchored", "manual_review_pending"],
                "provenance": {
                    "origin": "public_real_investor_qa",
                    "dataset": "CFQA",
                    "reviewer": "pending_independent_human_review",
                    "reviewed_at": "",
                    "cfqa_id": candidate.get("cfqa_id"),
                    "stock_code": code,
                    "company": candidate.get("company"),
                    "answer_pdf_pages": pages,
                },
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in output), encoding="utf-8")
    if args.unresolved_output:
        args.unresolved_output.parent.mkdir(parents=True, exist_ok=True)
        args.unresolved_output.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in unresolved), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "case_count": len(output), "unresolved": len(unresolved)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
