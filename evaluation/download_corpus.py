"""Download a public evaluation corpus and write a content-addressed snapshot.

The source manifest is committed; downloaded PDFs are deliberately ignored by
Git because disclosure files are large. The generated snapshot records the
exact local bytes used for later parsing, labeling, and evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_MANIFEST = ROOT / "evaluation" / "corpus" / "production_candidate_v1" / "sources.json"
DEFAULT_DOWNLOAD_DIR = ROOT / "evaluation" / "corpus" / "downloads" / "a-share-public-filings-candidate-v1"
REQUIRED_FIELDS = {"document_id", "security_code", "company", "report_period", "published_at", "title", "source_host", "source_url"}


def validate_sources_payload(payload: Any) -> dict[str, Any]:
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(documents, list) or not documents:
        raise ValueError("source manifest requires a non-empty documents list")
    ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("every document must be an object")
        missing = REQUIRED_FIELDS - set(document)
        if missing:
            raise ValueError(f"document missing required fields: {sorted(missing)}")
        document_id = str(document["document_id"])
        if document_id in ids:
            raise ValueError(f"duplicate document_id: {document_id}")
        ids.add(document_id)
        if not str(document["source_url"]).startswith("https://"):
            raise ValueError(f"{document_id}: source_url must use HTTPS")
    return payload


def load_sources(path: Path = DEFAULT_SOURCE_MANIFEST) -> dict[str, Any]:
    return validate_sources_payload(json.loads(path.read_text(encoding="utf-8")))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    """Prefer repository-relative paths, without rejecting external targets."""

    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _record_local_pdf(document: dict[str, Any], target: Path) -> dict[str, Any]:
    """Return snapshot metadata for a verified local PDF."""

    return {
        **document,
        "local_path": _display_path(target),
        "byte_size": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _download(
    document: dict[str, Any],
    target_dir: Path,
    *,
    timeout_seconds: float,
    reuse_existing: bool = False,
) -> dict[str, Any]:
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{document['document_id']}.pdf"
    temporary = target.with_suffix(".part")
    if reuse_existing and target.is_file():
        # Only reuse complete PDF files. A stale or partial file must be
        # downloaded again so a snapshot never silently records bad bytes.
        if target.read_bytes().startswith(b"%PDF"):
            return _record_local_pdf(document, target)
    response = requests.get(
        str(document["source_url"]),
        timeout=(10, timeout_seconds),
        headers={"User-Agent": "AlphaStock-Evaluation-Corpus/1.0"},
    )
    response.raise_for_status()
    content = response.content
    if not content.startswith(b"%PDF"):
        content_type = response.headers.get("content-type", "unknown")
        raise ValueError(f"{document['document_id']}: expected PDF bytes, got {content_type}")
    temporary.write_bytes(content)
    temporary.replace(target)
    return _record_local_pdf(document, target)


def download_corpus(
    source_manifest: Path = DEFAULT_SOURCE_MANIFEST,
    target_dir: Path = DEFAULT_DOWNLOAD_DIR,
    *,
    timeout_seconds: float = 90.0,
    limit: int | None = None,
    reuse_existing: bool = False,
) -> dict[str, Any]:
    source_manifest = source_manifest.resolve()
    target_dir = target_dir.resolve()
    sources = load_sources(source_manifest)
    documents = sources["documents"][:limit] if limit else sources["documents"]
    snapshot_documents = [
        _download(
            document,
            target_dir,
            timeout_seconds=timeout_seconds,
            reuse_existing=reuse_existing,
        )
        for document in documents
    ]
    snapshot_digest = hashlib.sha256(
        json.dumps(snapshot_documents, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "dataset_id": sources.get("dataset_id"),
        "source_manifest": _display_path(source_manifest),
        "downloaded_at": datetime.now(UTC).isoformat(),
        "document_count": len(snapshot_documents),
        "corpus_snapshot": f"sha256:{snapshot_digest}",
        "documents": snapshot_documents,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and hash AlphaStock public evaluation documents")
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--snapshot-out", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="reuse existing complete PDFs in target-dir and avoid re-downloading them",
    )
    args = parser.parse_args()
    snapshot = download_corpus(
        args.sources,
        args.target_dir,
        timeout_seconds=args.timeout_seconds,
        limit=args.limit,
        reuse_existing=args.reuse_existing,
    )
    args.snapshot_out.parent.mkdir(parents=True, exist_ok=True)
    args.snapshot_out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
