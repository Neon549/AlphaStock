"""Inspect a pinned PDF corpus before it enters a RAG evaluation pipeline.

This is intentionally a preflight, not a replacement for the project parser.
It detects whether the downloaded bytes still match the lock file and whether
PyMuPDF can extract enough page text. Low-text pages are explicit OCR/MinerU
review candidates instead of silently becoming empty retrieval chunks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import fitz


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCK = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "SNAPSHOT.json"
DEFAULT_DOWNLOAD_DIR = ROOT / "evaluation" / "corpus" / "downloads" / "a-share-public-filings-candidate-v1"
LOW_TEXT_THRESHOLD = 80


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_page_text(text: str, *, low_text_threshold: int = LOW_TEXT_THRESHOLD) -> dict[str, Any]:
    compact = "".join(text.split())
    return {
        "text_characters": len(compact),
        "low_text": len(compact) < low_text_threshold,
        "table_or_financial_marker": any(marker in compact for marker in ("单位", "项目", "资产负债表", "现金流量表", "利润表")),
    }


def load_lock(path: Path = DEFAULT_LOCK) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not str(payload.get("corpus_snapshot", "")).startswith("sha256:") or not isinstance(documents, list) or not documents:
        raise ValueError("snapshot lock must contain corpus_snapshot and documents")
    return payload


def inspect_document(record: dict[str, Any], download_dir: Path) -> dict[str, Any]:
    document_id = str(record["document_id"])
    path = download_dir / f"{document_id}.pdf"
    if not path.is_file():
        return {"document_id": document_id, "valid": False, "error": "missing_local_pdf"}
    actual_sha256 = _sha256(path)
    if actual_sha256 != record.get("sha256"):
        return {"document_id": document_id, "valid": False, "error": "sha256_mismatch", "actual_sha256": actual_sha256}

    with fitz.open(path) as pdf:
        page_summaries = [classify_page_text(page.get_text("text")) for page in pdf]
    page_count = len(page_summaries)
    low_text_pages = [index + 1 for index, page in enumerate(page_summaries) if page["low_text"]]
    financial_marker_pages = [index + 1 for index, page in enumerate(page_summaries) if page["table_or_financial_marker"]]
    extracted_characters = sum(page["text_characters"] for page in page_summaries)
    return {
        "document_id": document_id,
        "valid": True,
        "page_count": page_count,
        "extractable_pages": page_count - len(low_text_pages),
        "low_text_page_count": len(low_text_pages),
        "low_text_pages": low_text_pages,
        "text_coverage": round((page_count - len(low_text_pages)) / page_count, 4) if page_count else 0.0,
        "extracted_characters": extracted_characters,
        "financial_marker_page_count": len(financial_marker_pages),
        "needs_ocr_or_mineru_review": bool(low_text_pages),
    }


def preflight(lock_path: Path = DEFAULT_LOCK, download_dir: Path = DEFAULT_DOWNLOAD_DIR) -> dict[str, Any]:
    lock = load_lock(lock_path)
    documents = [inspect_document(record, download_dir) for record in lock["documents"]]
    valid_documents = [document for document in documents if document["valid"]]
    return {
        "corpus_snapshot": lock["corpus_snapshot"],
        "document_count": len(documents),
        "valid_documents": len(valid_documents),
        "total_pages": sum(document.get("page_count", 0) for document in valid_documents),
        "total_low_text_pages": sum(document.get("low_text_page_count", 0) for document in valid_documents),
        "documents": documents,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight a pinned AlphaStock PDF evaluation corpus")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = preflight(args.lock, args.download_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid_documents"] == report["document_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
