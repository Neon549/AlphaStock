"""Retrieval index for approved, versioned Agent-memory Markdown.

This is deliberately separate from document/news/strategy RAG.  Source files
are human-reviewed operating knowledge, not live financial evidence.  The
index only locates chunks; callers receive original Markdown text plus source
and hash for traceability.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MEMORY_KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)


@dataclass(frozen=True)
class MemoryChunk:
    source_path: str
    source_hash: str
    chunk_index: int
    content: str
    metadata: dict[str, str]

    @property
    def evidence_id(self) -> str:
        return f"memory:{self.source_path}:{self.source_hash[:12]}:{self.chunk_index}"


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip().lower()] = value.strip()
    return metadata, text[match.end():]


def _chunks(text: str) -> list[str]:
    """Heading-aware, overlap-preserving chunks without an LLM transformation."""
    body = text.strip()
    if not body:
        return []
    sections: list[str] = []
    matches = list(_HEADING.finditer(body))
    if not matches:
        sections = [body]
    else:
        if body[:matches[0].start()].strip():
            sections.append(body[:matches[0].start()].strip())
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            sections.append(body[match.start():end].strip())

    output: list[str] = []
    for section in sections:
        start = 0
        while start < len(section):
            end = min(len(section), start + CHUNK_SIZE)
            # Prefer paragraph/newline boundaries over arbitrary character cuts.
            if end < len(section):
                boundary = section.rfind("\n", start + CHUNK_SIZE // 2, end)
                if boundary > start:
                    end = boundary
            part = section[start:end].strip()
            if part:
                output.append(part)
            if end >= len(section):
                break
            start = max(end - CHUNK_OVERLAP, start + 1)
    return output


def approved_memory_files(root: Path = MEMORY_KNOWLEDGE_DIR) -> list[Path]:
    """Only Markdown explicitly marked ``status: approved`` can be indexed."""
    files: list[Path] = []
    for path in root.rglob("*.md") if root.exists() else []:
        metadata, _ = _front_matter(path.read_text(encoding="utf-8"))
        if metadata.get("status", "").lower() == "approved":
            files.append(path)
    return sorted(files)


def _file_chunks(path: Path, root: Path) -> list[MemoryChunk]:
    raw = path.read_text(encoding="utf-8")
    metadata, body = _front_matter(raw)
    source_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    source_path = path.relative_to(root).as_posix()
    return [
        MemoryChunk(source_path, source_hash, index, content, metadata)
        for index, content in enumerate(_chunks(body))
    ]


def _embed_local(texts: list[str]) -> list[list[float]]:
    """Reuse the project embedding model without hidden runtime downloads.

    Indexing is an explicit operational action. If the model was not preloaded,
    fail fast with a clear error instead of retrying remote downloads in an API
    worker or a CI job. The controlled preload can run with network access.
    """
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from rag.news_indexer import _embed

    return _embed(texts)


def sync_memory_index(root: Path = MEMORY_KNOWLEDGE_DIR, *, prune: bool = True) -> dict[str, Any]:
    """Replace indexed versions of approved files, optionally removing deleted files."""
    from db import get_conn
    from psycopg2.extras import Json

    root = root.resolve()
    files = approved_memory_files(root)
    all_chunks = [chunk for path in files for chunk in _file_chunks(path, root)]
    embeddings = _embed_local([chunk.content for chunk in all_chunks]) if all_chunks else []
    active_paths = {chunk.source_path for chunk in all_chunks}
    inserted = 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for path in files:
                source_path = path.relative_to(root).as_posix()
                cur.execute("DELETE FROM agent_memory_chunks WHERE source_path = %s", (source_path,))
            if prune:
                cur.execute("SELECT DISTINCT source_path FROM agent_memory_chunks")
                stale = [row[0] for row in cur.fetchall() if row[0] not in active_paths]
                for source_path in stale:
                    cur.execute("DELETE FROM agent_memory_chunks WHERE source_path = %s", (source_path,))
            for chunk, embedding in zip(all_chunks, embeddings):
                doc_id = hashlib.sha256(chunk.evidence_id.encode("utf-8")).hexdigest()
                cur.execute(
                    """
                    INSERT INTO agent_memory_chunks
                        (id, source_path, source_hash, chunk_index, content, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (
                        doc_id, chunk.source_path, chunk.source_hash, chunk.chunk_index,
                        chunk.content, Json(chunk.metadata), str(embedding),
                    ),
                )
                inserted += 1
        # get_conn deliberately only returns the pooled connection; unlike a
        # psycopg2 connection context manager it does not auto-commit.
        conn.commit()
    return {"files": len(files), "chunks": inserted, "root": str(root)}


def search_memory(query: str, *, top_k: int = 3) -> list[dict[str, Any]]:
    """Return approved original Markdown chunks; never inject the index itself."""
    if not query.strip():
        return []
    from db import get_conn
    embedding = _embed_local([query])[0]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_path, source_hash, chunk_index, content, metadata,
                       embedding <=> %s::vector AS distance
                FROM agent_memory_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (str(embedding), str(embedding), max(1, min(top_k, 8))),
            )
            rows = cur.fetchall()
    return [
        {
            "evidence_id": f"memory:{source_path}:{source_hash[:12]}:{chunk_index}",
            "source_path": source_path,
            "source_hash": source_hash,
            "chunk_index": chunk_index,
            "content": content,
            "metadata": metadata or {},
            "distance": float(distance),
        }
        for source_path, source_hash, chunk_index, content, metadata, distance in rows
    ]
