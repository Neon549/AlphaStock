"""Normalize public financial QA datasets into AlphaStock evaluation candidates.

The importer intentionally does not promote external QA records to RAG Gold.
CFQA's answer pages are only usable for retrieval scoring after the matching
annual-report PDF has been downloaded and its PDF page order has been pinned.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable


CFQA_URL = "https://github.com/ygan/CFQA"
CFQA_LICENSE = "MIT"
YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _load_json(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"expected a JSON array of objects: {path}")
    return value


def _load_fintruthqa(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _flatten_pages(value: Any) -> list[int]:
    pages: list[int] = []
    if isinstance(value, list):
        for item in value:
            pages.extend(_flatten_pages(item))
    elif isinstance(value, int):
        pages.append(value)
    return sorted(set(pages))


def _candidate_years(*values: str) -> list[int]:
    years: set[int] = set()
    for value in values:
        for match in YEAR_RE.findall(value or ""):
            years.add(int(match))
    return sorted(years)


def _stable_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_cfqa(
    rows: Iterable[dict[str, Any]],
    *,
    split: str,
    dataset_path: Path,
    repository_commit: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    dataset_sha = _stable_sha256(dataset_path)
    for row in rows:
        question = str(row.get("问题", "")).strip()
        answer = str(row.get("答案", "")).strip()
        if not question or not answer:
            continue
        company = str(row.get("公司", "")).strip()
        stock_code = str(row.get("股票代码", "")).strip()
        pages = _flatten_pages(row.get("答案出自"))
        years = _candidate_years(question, answer)
        record_id = str(row.get("id", "")).strip()
        normalized.append(
            {
                "id": f"cfqa-{split}-{record_id}",
                "split": "test" if split == "test" else "validation",
                "query": question,
                "reference_answer": answer,
                "source_type": "annual_report",
                "source_company": company,
                "stock_code": stock_code,
                "report_year_candidates": years,
                "answer_pdf_pages": pages,
                "evidence_status": "pdf_mapping_pending",
                "provenance": {
                    "origin": "public_real_investor_qa",
                    "dataset": "CFQA",
                    "dataset_url": CFQA_URL,
                    "license": CFQA_LICENSE,
                    "repository_commit": repository_commit,
                    "source_file_sha256": dataset_sha,
                    "imported_at": date.today().isoformat(),
                },
                "promotion_rule": "Download and pin the matching official annual-report PDF, map answer_pdf_pages to evidence IDs, then independently review before RAG Gold promotion.",
            }
        )
    return normalized


def normalize_fintruthqa(
    rows: Iterable[dict[str, Any]],
    *,
    dataset_path: Path,
    repository_commit: str,
) -> list[dict[str, Any]]:
    dataset_sha = _stable_sha256(dataset_path)
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        question = str(row.get("QUES", "")).strip()
        answer = str(row.get("ANS", "")).strip()
        if not question:
            continue
        normalized.append(
            {
                "id": f"fintruthqa-{index}",
                "split": "validation",
                "query": question,
                "reference_answer": answer,
                "source_type": "investor_interactive_qa",
                "evidence_status": "no_page_level_evidence",
                "quality_labels": {
                    "question_identification": row.get("IS_QUESTION"),
                    "question_relevance": row.get("QUES_RELEVANCE"),
                    "answer_readability": row.get("ANS_READABILITY"),
                    "answer_relevance": row.get("ANS_RELEVANCE"),
                },
                "provenance": {
                    "origin": "public_real_investor_qa",
                    "dataset": "FinTruthQA",
                    "dataset_url": "https://github.com/bethxx99/FinTruthQA",
                    "license": "Apache-2.0",
                    "repository_commit": repository_commit,
                    "source_file_sha256": dataset_sha,
                    "source_row": index,
                    "imported_at": date.today().isoformat(),
                },
                "evaluation_boundary": "Use for intent, slot, clarification and answer-quality stress testing; do not use as page-level RAG Recall/Precision without an independently mapped source document.",
            }
        )
    return normalized


def _sample_rows(rows: list[dict[str, Any]], *, sample_size: int | None, seed: int) -> list[dict[str, Any]]:
    if not sample_size or sample_size >= len(rows):
        return rows
    if sample_size <= 0:
        raise ValueError("sample size must be positive")
    return random.Random(seed).sample(rows, sample_size)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import CFQA into AlphaStock external QA candidates")
    parser.add_argument("--repo", type=Path, required=True, help="Local CFQA repository")
    parser.add_argument("--dataset", choices=("cfqa", "fintruthqa"), default="cfqa")
    parser.add_argument("--split", choices=("train", "dev", "test"), default="test")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True, help="Pinned source repository commit")
    parser.add_argument("--sample-size", type=int, help="Deterministically sample this many rows")
    parser.add_argument("--seed", type=int, default=20260812)
    args = parser.parse_args()

    if args.dataset == "cfqa":
        source = args.repo / "dataset" / "split_by_company" / f"split_by_company_{args.split}.json"
    else:
        source = args.repo / "dataset" / "FinTruthQA.csv"
    if not source.exists():
        raise SystemExit(f"missing {args.dataset} source: {source}")
    rows = (
        normalize_cfqa(
            _load_json(source),
            split=args.split,
            dataset_path=source,
            repository_commit=args.commit,
        )
        if args.dataset == "cfqa"
        else normalize_fintruthqa(
            _load_fintruthqa(source),
            dataset_path=source,
            repository_commit=args.commit,
        )
    )
    rows = _sample_rows(rows, sample_size=args.sample_size, seed=args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"output": str(args.output), "dataset": args.dataset, "case_count": len(rows), "source": str(source), "seed": args.seed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
