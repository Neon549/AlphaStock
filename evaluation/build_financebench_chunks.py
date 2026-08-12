"""Create page-citable retrieval chunks for the FinanceBench snapshot.

FinanceBench evidence is annotated at the PDF-page level. A full extracted
page can be thousands of characters, which is too broad a BM25/Dense unit.
This utility splits page text for ranking but preserves a ``page_evidence_id``
backlink so the published Gold page labels remain the evaluation target.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAGES = ROOT / "runtime" / "reports" / "financebench-v1.pages.jsonl"
DEFAULT_OUT = ROOT / "runtime" / "reports" / "financebench-v1.chunks-1200.jsonl"


def _chunk_text(text: str, *, size: int, overlap: int) -> list[str]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk size must be positive and overlap must be smaller than it")
    text = text.strip()
    if not text:
        return [""]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
            if boundary > start:
                end = boundary
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_chunks(pages: Iterable[dict[str, Any]], *, size: int = 1200, overlap: int = 180) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page in pages:
        page_evidence_id = str(page["evidence_id"])
        for chunk_index, text in enumerate(_chunk_text(str(page.get("text", "")), size=size, overlap=overlap)):
            chunks.append({
                **page,
                "evidence_id": f"{page_evidence_id}:c{chunk_index}",
                "page_evidence_id": page_evidence_id,
                "text": text,
                "chunk_index": chunk_index,
            })
    return chunks


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build page-citable FinanceBench retrieval chunks")
    parser.add_argument("--pages", type=Path, default=DEFAULT_PAGES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--chunk-chars", type=int, default=1200)
    parser.add_argument("--overlap-chars", type=int, default=180)
    args = parser.parse_args()
    pages = _load_jsonl(args.pages)
    chunks = build_chunks(pages, size=args.chunk_chars, overlap=args.overlap_chars)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in chunks), encoding="utf-8")
    print(json.dumps({"pages": len(pages), "chunks": len(chunks), "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
