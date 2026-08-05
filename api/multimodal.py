#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
@author: yulin
@created: 2026/7/17 21:20
@updated: 2026/7/17 21:20
@version: 1.0
@description: 
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
api/multimodal.py
多模态处理器

支持：
  1. 图片分析（财报截图/K线图）→ Qwen-VL 提取数据
  2. PDF/文档解析 → 分块入 PostgreSQL + pgvector → RAG检索
  3. 用户级隔离：每个session独立Collection，对话结束清理

大厂方案：
  图片：多模态LLM直接理解，不OCR
  文档：解析→分块→临时向量库→检索增强
"""

import hashlib
from datetime import datetime
from pathlib import Path
import re

from rag.news_indexer import _embed
from api.multimodal_vision import analyze_image
from api.document_processing.chunking import build_hierarchical_chunks
from api.document_processing.parsers import parse_document_pages
from api.document_processing import repository as document_repository


# ── 临时文档向量库（PostgreSQL + pgvector）────────────────────────────────

COLLECTION_TTL_HOURS = 2  # 2小时后自动清理

_EVIDENCE_ID_PATTERN = re.compile(r"evidence_id=([^|\]]+)")
_EVIDENCE_FIELD_PATTERN = re.compile(r"(文件|章节|版本)=([^|\]]+)")
_EVIDENCE_PAGE_PATTERN = re.compile(r"第\s*(\d+)\s*页")


def cleanup_session(session_id: str):
    """清理指定 session 的临时文档，不保留本地向量库副本。"""
    try:
        deleted = document_repository.cleanup_session(session_id)
        print(f"[Multimodal] 清理 session {session_id[:8]} 的临时文档：{deleted} 块")
    except Exception as exc:
        print(f"[Multimodal] 清理临时文档失败: {exc}")


def cleanup_document(session_id: str, document_id: str) -> int:
    """只清理会话中的一份文档，供前端移除附件时调用。"""
    try:
        return document_repository.cleanup_document(session_id, document_id)
    except Exception as exc:
        print(f"[Multimodal] 清理单个临时文档失败: {exc}")
        return 0


def cleanup_expired_sessions():
    """清理超过 TTL 的临时文档，返回删除块数。"""
    try:
        return document_repository.cleanup_expired(COLLECTION_TTL_HOURS)
    except Exception as exc:
        print(f"[Multimodal] 清理过期临时文档失败: {exc}")
        return 0


def index_image_analysis(
    image_bytes: bytes,
    filename: str,
    session_id: str,
    vlm_result: dict,
    question: str = "",
) -> dict:
    """Store a successful VLM extraction as session-scoped pgvector evidence.

    The image itself is not embedded.  We embed the VLM-extracted text, while retaining
    the image filename and a dedicated parent path so later citations remain honest about
    their source.  Images do not have a reliable document page number and use page=0.
    """
    extracted_data = (vlm_result.get("extracted_data") or "").strip()
    data_type = vlm_result.get("data_type", "unknown")
    if not extracted_data or data_type == "unknown":
        return {"success": False, "indexed": False, "error": "VLM 未返回可索引的识别结果"}

    image_type_label = {
        "financial": "财报或财务截图",
        "kline": "K线或技术图表",
        "other": "公告、新闻或其他图片",
    }.get(data_type, "图片")
    source_text = (
        "# 图像识别结果\n"
        f"## 图像类型\n{image_type_label}\n"
        f"## 用户问题\n{question or '未提供'}\n"
        f"## VLM 提取事实\n{extracted_data}"
    )
    chunks = build_hierarchical_chunks([(0, source_text)])
    if not chunks:
        return {"success": False, "indexed": False, "error": "图像识别结果分块失败"}

    document_id = hashlib.sha256(image_bytes).hexdigest()[:16]
    storage_id = hashlib.sha256(f"{session_id}:{document_id}".encode("utf-8")).hexdigest()[:16]
    ids = [f"img_{storage_id}_{index}" for index in range(len(chunks))]
    document_version = "qwen-vl-plus-extraction-v1"
    uploaded_at = datetime.now().isoformat()

    try:
        embeddings = _embed([chunk["text"] for chunk in chunks])
        document_repository.replace_chunks(
            session_id=session_id,
            filename=filename,
            document_id=document_id,
            document_version=document_version,
            ids=ids,
            chunks=chunks,
            embeddings=embeddings,
            uploaded_at=uploaded_at,
        )
    except Exception as exc:
        return {"success": False, "indexed": False, "error": f"VLM 结果写入 pgvector 失败：{exc}"}

    return {
        "success": True,
        "indexed": True,
        "document_id": document_id,
        "chunk_count": len(chunks),
        "document_version": document_version,
    }


# ── 文档处理（PDF/Word/TXT）──────────────────────────────────────────────

def process_document(
    file_bytes: bytes,
    filename: str,
    session_id: str,
) -> dict:
    """
    处理上传的文档，解析后存入临时向量库

    Args:
        file_bytes: 文件二进制数据
        filename: 文件名（用于判断格式）
        session_id: 用户session ID（隔离用）

    Returns:
        {
            "success": True,
            "chunk_count": 15,
            "preview": "文档内容预览...",
            "file_type": "pdf"
        }
    """
    ext = Path(filename).suffix.lower()
    # 解析文档内容。PDF 保留页码；其它格式没有可靠页码时以 0 表示。
    try:
        pages, parser = parse_document_pages(file_bytes, filename)
    except Exception as e:
        return {"success": False, "error": f"文档解析失败：{e}"}

    text = "\n\n".join(page_text for _, page_text in pages if page_text.strip())
    if not text.strip():
        return {"success": False, "error": "文档内容为空"}

    document_id = hashlib.sha256(file_bytes).hexdigest()[:16]
    chunks = build_hierarchical_chunks(pages)

    if not chunks:
        return {"success": False, "error": "文档分块失败"}

    # 子块用于向量召回；parent_path、页码和相邻子块 ID 用于命中后的上下文补全。
    # session 级文档量较小，因此不建 HNSW；查询先按 session 过滤后精确向量排序。
    uploaded_at = datetime.now().isoformat()
    # 主键必须同时隔离 session；同一文件可被不同用户/会话上传。
    storage_id = hashlib.sha256(f"{session_id}:{document_id}".encode("utf-8")).hexdigest()[:16]
    ids = [f"doc_{storage_id}_{i}" for i in range(len(chunks))]
    embeddings = _embed([chunk["text"] for chunk in chunks])
    try:
        document_repository.replace_chunks(
            session_id=session_id,
            filename=filename,
            document_id=document_id,
            document_version=document_id,
            ids=ids,
            chunks=chunks,
            embeddings=embeddings,
            uploaded_at=uploaded_at,
        )
    except Exception as exc:
        return {"success": False, "error": f"临时文档写入 PostgreSQL 失败：{exc}"}

    return {
        "success": True,
        "chunk_count": len(chunks),
        "preview": text[:300] + "..." if len(text) > 300 else text,
        "file_type": ext.lstrip("."),
        "total_chars": len(text),
        "document_id": document_id,
        "retrieval_mode": "hierarchical_child_chunk_with_neighbor_context",
        "parser": parser,
    }


def retrieve_from_document(session_id: str, query: str, k: int = 5) -> str:
    """
    从用户上传的文档中检索相关内容

    Args:
        session_id: 用户session ID
        query: 检索问题
        k: 返回条数

    Returns:
        检索到的文档内容字符串
    """
    try:
        query_embedding = _embed([query])[0]
        rows = document_repository.search_chunks(session_id, query_embedding, k)
        if not rows:
            return ""
        rendered: list[str] = []
        included_ids: set[str] = set()
        for row in rows:
            chunk_id, chunk, *metadata_values = row
            metadata = _document_row_metadata(metadata_values)
            _append_evidence(rendered, included_ids, chunk_id, chunk, metadata, "命中子块")

            # 命中精确子块后，最多补一个相邻块。它能补齐被切分的表格说明或限定条件，
            # 又不会像直接塞整章那样明显拉高 token 成本。
            for neighbor_id in (metadata.get("previous_id"), metadata.get("next_id")):
                if not neighbor_id or neighbor_id in included_ids:
                    continue
                neighbor = document_repository.get_chunk(session_id, neighbor_id)
                if neighbor:
                    neighbor_id, neighbor_text, *neighbor_metadata_values = neighbor
                    _append_evidence(
                        rendered,
                        included_ids,
                        neighbor_id,
                        neighbor_text,
                        _document_row_metadata(neighbor_metadata_values),
                        "相邻上下文",
                    )

        return "\n\n".join(rendered)

    except Exception as e:
        print(f"[Multimodal] 文档检索失败: {e}")
        return ""


def extract_document_citations(document_evidence: str) -> list[dict]:
    """从供模型使用的证据块提取可直接返回给前端的引用元数据。"""
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


# ── 文档解析器 ────────────────────────────────────────────────────────────

def _document_row_metadata(values: list) -> dict:
    """将 PostgreSQL 查询行转换为统一证据 metadata。"""
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
    """格式化可回链的检索证据，供下游 prompt 与最终报告引用。"""
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
