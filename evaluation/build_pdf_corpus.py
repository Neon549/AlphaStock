"""Build a page-citable candidate corpus from a frozen local PDF snapshot.

PyMuPDF supplies the deterministic baseline text layer. Pages with too little
text are retained in the manifest but excluded from text chunks, so a later
MinerU/OCR enrichment pass cannot silently create empty evidence. The output
is a candidate corpus for human labeling, not an automatically trusted index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import fitz

from evaluation.corpus_preflight import LOW_TEXT_THRESHOLD, load_lock
from evaluation.download_corpus import DEFAULT_SOURCE_MANIFEST, load_sources


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOWNLOAD_DIR = ROOT / "evaluation" / "corpus" / "downloads" / "a-share-public-filings-candidate-v1"
DEFAULT_LOCK = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "SNAPSHOT.json"
SECTION_PATTERN = re.compile(
    r"^(第[一二三四五六七八九十百零〇0-9]+[章节部分].{0,60}|[（(]?[一二三四五六七八九十0-9]+[）)]?[、.]\s*[^\n]{2,60})$",
    flags=re.MULTILINE,
)


def _normalise(text: str) -> str:
    return "\n".join(line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip())


def section_heading(text: str) -> str | None:
    match = SECTION_PATTERN.search(_normalise(text))
    return match.group(1).strip() if match else None


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """Create bounded overlapping chunks without splitting the same text twice."""

    normalised = _normalise(text)
    if not normalised:
        return []
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap must be smaller than chunk_size")
    chunks: list[str] = []
    start = 0
    while start < len(normalised):
        end = min(len(normalised), start + chunk_size)
        if end < len(normalised):
            boundary = max(normalised.rfind("\n", start, end), normalised.rfind("。", start, end))
            if boundary > start + chunk_size // 2:
                end = boundary + 1
        piece = normalised[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(normalised):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _source_by_id(path: Path = DEFAULT_SOURCE_MANIFEST) -> dict[str, dict[str, Any]]:
    return {str(document["document_id"]): document for document in load_sources(path)["documents"]}


def build_corpus(
    *,
    lock_path: Path = DEFAULT_LOCK,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
    chunk_size: int = 600,
    overlap: int = 80,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lock = load_lock(lock_path)
    sources = _source_by_id(source_manifest)
    chunks: list[dict[str, Any]] = []
    document_summaries: list[dict[str, Any]] = []

    for record in lock["documents"]:
        document_id = str(record["document_id"])
        source = sources[document_id]
        pdf_path = download_dir / f"{document_id}.pdf"
        if not pdf_path.is_file():
            raise FileNotFoundError(f"missing pinned PDF: {pdf_path}")
        current_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        if current_hash != record["sha256"]:
            raise ValueError(f"{document_id}: PDF hash differs from snapshot lock")

        section_path = [str(source["title"])]
        page_count = low_text_pages = document_chunks = 0
        with fitz.open(pdf_path) as pdf:
            page_count = len(pdf)
            for page_number, page in enumerate(pdf, start=1):
                text = _normalise(page.get_text("text"))
                if len("".join(text.split())) < LOW_TEXT_THRESHOLD:
                    low_text_pages += 1
                    continue
                heading = section_heading(text)
                if heading:
                    section_path = [str(source["title"]), heading]
                for index, piece in enumerate(chunk_text(text, chunk_size=chunk_size, overlap=overlap)):
                    chunks.append(
                        {
                            "evidence_id": f"{document_id}:p{page_number}:c{index}",
                            "document_id": document_id,
                            "security_code": source["security_code"],
                            "report_period": source["report_period"],
                            "published_at": source["published_at"],
                            "page": page_number,
                            "parent_path": section_path,
                            "text": piece,
                            "parser": "pymupdf_text",
                            "source_sha256": current_hash,
                        }
                    )
                    document_chunks += 1
        document_summaries.append(
            {
                "document_id": document_id,
                "page_count": page_count,
                "low_text_pages_excluded": low_text_pages,
                "chunk_count": document_chunks,
                "source_sha256": current_hash,
            }
        )

    chunk_digest = hashlib.sha256(
        "\n".join(json.dumps(chunk, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for chunk in chunks).encode("utf-8")
    ).hexdigest()
    metadata = {
        "corpus_snapshot": lock["corpus_snapshot"],
        "candidate_index_snapshot": f"sha256:{chunk_digest}",
        "parser": "pymupdf_text",
        "chunk_size": chunk_size,
        "overlap": overlap,
        "document_count": len(document_summaries),
        "chunk_count": len(chunks),
        "documents": document_summaries,
        "warning": "Low-text pages are excluded pending MinerU/OCR enrichment and must not be treated as negative evidence.",
    }
    return metadata, chunks


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build page-citable candidate chunks from a frozen PDF corpus")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--chunks-out", type=Path, required=True)
    parser.add_argument("--metadata-out", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--overlap", type=int, default=80)
    args = parser.parse_args()
    metadata, chunks = build_corpus(
        lock_path=args.lock,
        download_dir=args.download_dir,
        source_manifest=args.source_manifest,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )
    write_jsonl(args.chunks_out, chunks)
    args.metadata_out.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_out.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
