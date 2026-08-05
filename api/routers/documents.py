"""Authenticated document and image upload endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from api.multimodal import (
    analyze_image,
    cleanup_document,
    cleanup_session,
    index_image_analysis,
    process_document,
)
from api.security import claim_session, require_actor
from control_plane.security import SecurityOperation, authorize_operation


router = APIRouter(tags=["documents"])

_ALLOWED_DOCUMENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
    "text/csv",
    "application/vnd.ms-excel",
}


def _authorize_upload(actor_id: str, session_id: str) -> None:
    try:
        authorize_operation(
            SecurityOperation(
                tool="upload", target="document", actor_id=actor_id, session_id=session_id
            ),
            mode="auto",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="operation is not permitted") from exc


def _owned_session(
    session_id: str,
    x_auth_token: str | None,
    authorization: str | None,
) -> tuple[str, str]:
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    return actor_id, claim_session(session_id, actor_id)


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    question: str = Form(default=""),
    session_id: str = Form(default="default_session"),
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id, session_id = _owned_session(session_id, x_auth_token, authorization)
    _authorize_upload(actor_id, session_id)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片文件")
    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过10MB")
    result = analyze_image(file_bytes, file.content_type, question)
    index_result = index_image_analysis(
        image_bytes=file_bytes,
        filename=file.filename or "uploaded-image",
        session_id=session_id,
        vlm_result=result,
        question=question,
    )
    return {
        "filename": file.filename,
        "extracted_data": result["extracted_data"],
        "analysis": result["analysis"],
        "data_type": result["data_type"],
        "session_id": session_id,
        "document_id": index_result.get("document_id"),
        "indexed": index_result.get("indexed", False),
        "chunk_count": index_result.get("chunk_count", 0),
        "index_error": index_result.get("error"),
        "status": "success",
    }


@router.post("/upload/document")
async def upload_document(
    file: UploadFile = File(...),
    session_id: str = Form(default="default_session"),
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id, session_id = _owned_session(session_id, x_auth_token, authorization)
    _authorize_upload(actor_id, session_id)
    if file.content_type not in _ALLOWED_DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail="unsupported document type")
    file_bytes = await file.read()
    if len(file_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过20MB")
    result = process_document(file_bytes, file.filename or "uploaded-document", session_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "文档处理失败"))
    return {
        "filename": file.filename,
        "chunk_count": result["chunk_count"],
        "preview": result["preview"],
        "file_type": result["file_type"],
        "total_chars": result["total_chars"],
        "document_id": result["document_id"],
        "session_id": session_id,
        "status": "success",
        "message": f"文档已处理，共{result['chunk_count']}个片段，分析时将自动参考此文档",
    }


@router.delete("/upload/session/{session_id}")
def cleanup_session_route(
    session_id: str,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id, session_id = _owned_session(session_id, x_auth_token, authorization)
    cleanup_session(session_id)
    from db import execute

    execute("DELETE FROM upload_sessions WHERE session_id = %s AND actor_id = %s", (session_id, actor_id))
    return {"status": "ok", "message": f"session {session_id[:8]} 已清理"}


@router.delete("/upload/session/{session_id}/document/{document_id}")
def cleanup_document_route(
    session_id: str,
    document_id: str,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    _actor_id, session_id = _owned_session(session_id, x_auth_token, authorization)
    return {"status": "ok", "deleted_chunks": cleanup_document(session_id, document_id)}
