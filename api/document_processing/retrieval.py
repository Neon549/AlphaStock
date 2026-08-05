"""Retrieval and evidence rendering for user-uploaded documents."""

from __future__ import annotations

import re

from rag.news_indexer import _embed
from api.document_processing import repository


_EVIDENCE_ID_PATTERN = re.compile(r"evidence_id=([^|\]]+)")
_EVIDENCE_FIELD_PATTERN = re.compile(r"(文件|章节|版本)=([^|\]]+)")
_EVIDENCE_PAGE_PATTERN = re.compile(r"第\s*(\d+)\s*页")


def retrieve_document_context(session_id: str, query: str, k: int = 5) -> str:
    """Retrieve child chunks and at most one neighbour per hit as evidence."""
    try:
        query_embedding = _embed([query])[0]
        rows = repository.search_chunks(session_id, query_embedding, k)
        if not rows:
            return ""

        rendered: list[str] = []
        included_ids: set[str] = set()
        for row in rows:
            chunk_id, chunk, *metadata_values = row
            metadata = _row_metadata(metadata_values)
            _append_evidence(rendered, included_ids, chunk_id, chunk, metadata, "命中子块")
            for neighbor_id in (metadata.get("previous_id"), metadata.get("next_id")):
                if not neighbor_id or neighbor_id in included_ids:
                    continue
                neighbor = repository.get_chunk(session_id, neighbor_id)
                if neighbor:
                    next_id, next_text, *next_metadata_values = neighbor
                    _append_evidence(
                        rendered,
                        included_ids,
                        next_id,
                        next_text,
                        _row_metadata(next_metadata_values),
                        "相邻上下文",
                    )
        return "\n\n".join(rendered)
    except Exception as exc:
        print(f"[DocumentRetrieval] 文档检索失败: {exc}")
        return ""


def extract_document_citations(document_evidence: str) -> list[dict]:
    """Convert evidence headers into frontend-safe citation metadata."""
    citations: list[dict] = []
    seen: set[str] = set()
    for header in re.findall(r"\[[^\]]*evidence_id=[^\]]+\]", document_evidence or ""):
        id_match = _EVIDENCE_ID_PATTERN.search(header)
        if not id_match:
            continue
        evidence_id = id_match.group(1).strip()
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        fields = {key: value.strip() for key, value in _EVIDENCE_FIELD_PATTERN.findall(header)}
        page_match = _EVIDENCE_PAGE_PATTERN.search(header)
        citations.append(
            {
                "evidence_id": evidence_id,
                "filename": fields.get("文件", ""),
                "section": fields.get("章节", "正文"),
                "page": int(page_match.group(1)) if page_match else None,
                "document_version": fields.get("版本", ""),
            }
        )
    return citations


def _row_metadata(values: list) -> dict:
    filename, document_version, page, parent_path, previous_id, next_id = values
    return {
        "filename": filename,
        "document_version": document_version,
        "page": page,
        "parent_path": parent_path,
        "previous_id": previous_id,
        "next_id": next_id,
    }


def _append_evidence(
    rendered: list[str],
    included_ids: set[str],
    chunk_id: str,
    text: str,
    metadata: dict,
    label: str,
) -> None:
    if chunk_id in included_ids:
        return
    included_ids.add(chunk_id)
    page = metadata.get("page", 0)
    page_text = f"第 {page} 页" if page else "无页码"
    rendered.append(
        f"[{label} | evidence_id={chunk_id} | 文件={metadata.get('filename', '')} | "
        f"章节={metadata.get('parent_path', '正文')} | {page_text} | "
        f"版本={metadata.get('document_version', '')}]\n{text}"
    )
