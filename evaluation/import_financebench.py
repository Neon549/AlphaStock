"""Import the public human-annotated FinanceBench sample into AlphaStock.

FinanceBench publishes 150 open-source questions with human answers,
justifications, evidence text, zero-indexed PDF pages, and the corresponding
PDFs.  This importer keeps that external provenance intact and creates a
page-level corpus so retrieval can be evaluated without relabeling the source
benchmark as an AlphaStock production test set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

import fitz


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPO = ROOT / "runtime" / "external" / "financebench"
DEFAULT_OUT_DIR = ROOT / "evaluation" / "corpus" / "financebench_v1"
DEFAULT_PAGES_OUT = ROOT / "runtime" / "reports" / "financebench-v1.pages.jsonl"
DEFAULT_PAGES_META_OUT = ROOT / "runtime" / "reports" / "financebench-v1.pages.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _repository_commit(repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "public-release-unknown-commit"
    return result.stdout.strip()


def _report_period(doc_name: str, doc_period: Any) -> str:
    match = re.search(r"_(20\d{2}(?:Q[1-4])?)_", doc_name)
    if match:
        token = match.group(1)
        return token if "Q" in token else f"FY{token}"
    return f"FY{doc_period}"


def _source_manifest(
    questions: Iterable[dict[str, Any]],
    metadata: Iterable[dict[str, Any]],
    *,
    repo: Path,
    repository_commit: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    used_doc_names = {str(row["doc_name"]) for row in questions}
    metadata_by_name = {str(row["doc_name"]): row for row in metadata}
    documents: list[dict[str, Any]] = []
    for doc_name in sorted(used_doc_names):
        meta = metadata_by_name.get(doc_name)
        if not meta:
            raise ValueError(f"FinanceBench metadata missing document: {doc_name}")
        pdf_path = repo / "pdfs" / f"{doc_name}.pdf"
        if not pdf_path.is_file():
            raise FileNotFoundError(f"FinanceBench PDF missing: {pdf_path}")
        documents.append({
            "document_id": doc_name,
            "security_code": doc_name,
            "company": str(meta["company"]),
            "report_period": _report_period(doc_name, meta.get("doc_period")),
            "published_at": f"{int(meta.get('doc_period') or 0):04d}-12-31",
            "title": doc_name,
            "source_host": "financebench-public-repository",
            "source_url": str(meta["doc_link"]),
            "local_path": str(pdf_path.resolve().relative_to(ROOT.resolve())).replace("\\", "/"),
            "sha256": _sha256(pdf_path),
        })
    snapshot_payload = {
        "dataset_id": "financebench-open-source-v1",
        "repository": "https://github.com/patronus-ai/financebench",
        "repository_commit": repository_commit,
        "documents": documents,
    }
    corpus_digest = hashlib.sha256(
        json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest = {
        "dataset_id": "financebench-open-source-v1",
        "dataset_url": "https://github.com/patronus-ai/financebench",
        "repository_commit": repository_commit,
        "source_license": "See FinanceBench repository LICENSE",
        "corpus_snapshot": f"sha256:{corpus_digest}",
        "documents": documents,
    }
    return manifest, documents


def build_cases(
    rows: Iterable[dict[str, Any]],
    *,
    corpus_snapshot: str,
    repository_commit: str,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for row in rows:
        evidence = row.get("evidence") or []
        page_keys = sorted({
            (str(item.get("doc_name") or row["doc_name"]), int(item["evidence_page_num"]) + 1)
            for item in evidence
        })
        relevant_ids = [f"{doc_name}:p{page}:c0" for doc_name, page in page_keys]
        citations = [
            {"filename": f"{doc_name}.pdf", "page": page, "section": ""}
            for doc_name, page in page_keys
        ]
        cases.append({
            "id": f"financebench-open-{row['financebench_id']}",
            "split": "test",
            "source_type": "public_sec_filing",
            "corpus_version": corpus_snapshot,
            "query": str(row["question"]),
            "reference_answer": str(row["answer"]),
            "expected": {
                "answer_facts": [],
                "relevant_evidence_ids": relevant_ids,
                "required_citations": citations,
                "abstain_allowed": False,
            },
            "gold_annotation": {
                "company": str(row.get("company", "")),
                "doc_name": str(row["doc_name"]),
                "justification": str(row.get("justification", "")),
                "question_type": row.get("question_type"),
                "question_reasoning": row.get("question_reasoning"),
                "evidence_texts": [str(item.get("evidence_text", "")) for item in evidence],
                "zero_indexed_source_pages": [int(item["evidence_page_num"]) for item in evidence],
            },
            "provenance": {
                "origin": "public_human_annotated_financebench",
                "dataset": "FinanceBench open-source sample",
                "dataset_url": "https://github.com/patronus-ai/financebench",
                "repository_commit": repository_commit,
                "reviewer": "FinanceBench human annotators",
                "reviewed_at": "2023-11-20",
                "note": "reviewed_at is the public benchmark release date; AlphaStock did not relabel these cases.",
            },
        })
    return cases


def build_page_corpus(documents: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for source in documents:
        pdf_path = ROOT / str(source["local_path"])
        page_count = 0
        text_pages = 0
        with fitz.open(pdf_path) as pdf:
            page_count = len(pdf)
            for page_number, page in enumerate(pdf, start=1):
                text = page.get_text("text").replace("\r", "\n").strip()
                if text:
                    text_pages += 1
                pages.append({
                    "evidence_id": f"{source['document_id']}:p{page_number}:c0",
                    "document_id": source["document_id"],
                    "security_code": source["security_code"],
                    "report_period": source["report_period"],
                    "published_at": source["published_at"],
                    "page": page_number,
                    "parent_path": [source["title"]],
                    "text": text,
                    "parser": "pymupdf_text",
                    "source_sha256": source["sha256"],
                })
        summaries.append({
            "document_id": source["document_id"],
            "page_count": page_count,
            "text_pages": text_pages,
            "source_sha256": source["sha256"],
        })
    page_digest = hashlib.sha256(
        "\n".join(json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for page in pages).encode("utf-8")
    ).hexdigest()
    metadata = {
        "dataset_id": "financebench-open-source-v1",
        "parser": "pymupdf_text",
        "granularity": "one page per evidence item",
        "page_count": len(pages),
        "document_count": len(summaries),
        "candidate_index_snapshot": f"sha256:{page_digest}",
        "documents": summaries,
    }
    return pages, metadata


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def run_import(
    *,
    repo: Path = DEFAULT_REPO,
    out_dir: Path = DEFAULT_OUT_DIR,
    pages_out: Path = DEFAULT_PAGES_OUT,
    pages_meta_out: Path = DEFAULT_PAGES_META_OUT,
) -> dict[str, Any]:
    questions_path = repo / "data" / "financebench_open_source.jsonl"
    metadata_path = repo / "data" / "financebench_document_information.jsonl"
    questions = _load_jsonl(questions_path)
    metadata = _load_jsonl(metadata_path)
    repository_commit = _repository_commit(repo)
    source_manifest, documents = _source_manifest(
        questions,
        metadata,
        repo=repo,
        repository_commit=repository_commit,
    )
    cases = build_cases(
        questions,
        corpus_snapshot=source_manifest["corpus_snapshot"],
        repository_commit=repository_commit,
    )
    pages, page_metadata = build_page_corpus(documents)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sources.json").write_text(json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "SNAPSHOT.json").write_text(json.dumps({
        "dataset_id": "financebench-open-source-v1",
        "dataset_url": "https://github.com/patronus-ai/financebench",
        "repository_commit": repository_commit,
        "source_manifest": str((out_dir / "sources.json").relative_to(ROOT)).replace("\\", "/"),
        "corpus_snapshot": source_manifest["corpus_snapshot"],
        "document_count": len(documents),
        "case_count": len(cases),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_jsonl(out_dir / "rag_public_gold.jsonl", cases)
    _write_jsonl(pages_out, pages)
    pages_meta_out.parent.mkdir(parents=True, exist_ok=True)
    pages_meta_out.write_text(json.dumps(page_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "cases": len(cases),
        "documents": len(documents),
        "pages": len(pages),
        "repository_commit": repository_commit,
        "corpus_snapshot": source_manifest["corpus_snapshot"],
        "cases_out": str(out_dir / "rag_public_gold.jsonl"),
        "pages_out": str(pages_out),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import public human-annotated FinanceBench into AlphaStock")
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--pages-out", type=Path, default=DEFAULT_PAGES_OUT)
    parser.add_argument("--pages-meta-out", type=Path, default=DEFAULT_PAGES_META_OUT)
    args = parser.parse_args()
    print(json.dumps(run_import(repo=args.repo, out_dir=args.out_dir, pages_out=args.pages_out, pages_meta_out=args.pages_meta_out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
