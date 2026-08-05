"""PostgreSQL + pgvector persistence for session-scoped uploaded evidence."""

from __future__ import annotations

from datetime import datetime

from db import get_conn


def cleanup_session(session_id: str) -> int:
    return _delete("DELETE FROM uploaded_document_chunks WHERE session_id = %s", (session_id,))


def cleanup_document(session_id: str, document_id: str) -> int:
    return _delete(
        "DELETE FROM uploaded_document_chunks WHERE session_id = %s AND document_id = %s",
        (session_id, document_id),
    )


def cleanup_expired(ttl_hours: int) -> int:
    return _delete(
        "DELETE FROM uploaded_document_chunks WHERE created_at < NOW() - (%s * INTERVAL '1 hour')",
        (ttl_hours,),
    )


def replace_chunks(
    *,
    session_id: str,
    filename: str,
    document_id: str,
    document_version: str,
    ids: list[str],
    chunks: list[dict],
    embeddings: list,
    uploaded_at: str | None = None,
) -> None:
    """Atomically replace one document's session-scoped vector chunks."""
    if len(ids) != len(chunks) or len(chunks) != len(embeddings):
        raise ValueError("文档块、ID 和 embedding 数量不一致")
    uploaded_at = uploaded_at or datetime.now().isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM uploaded_document_chunks WHERE session_id = %s AND document_id = %s",
                (session_id, document_id),
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                cur.execute(
                    """
                    INSERT INTO uploaded_document_chunks
                        (id, session_id, filename, document_id, document_version,
                         chunk_index, page, parent_path, previous_id, next_id,
                         content, embedding, created_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
                    """,
                    (
                        ids[index], session_id, filename, document_id, document_version,
                        index, chunk["page"], chunk["parent_path"],
                        ids[index - 1] if index > 0 else None,
                        ids[index + 1] if index + 1 < len(ids) else None,
                        chunk["text"], str(embedding), uploaded_at,
                    ),
                )
        conn.commit()


def search_chunks(session_id: str, query_embedding, limit: int) -> list:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content, filename, document_version, page, parent_path,
                       previous_id, next_id
                FROM uploaded_document_chunks
                WHERE session_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (session_id, str(query_embedding), limit),
            )
            return cur.fetchall()


def get_chunk(session_id: str, chunk_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content, filename, document_version, page, parent_path,
                       previous_id, next_id
                FROM uploaded_document_chunks
                WHERE session_id = %s AND id = %s
                """,
                (session_id, chunk_id),
            )
            return cur.fetchone()


def _delete(sql: str, params: tuple) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            deleted = cur.rowcount
        conn.commit()
        return deleted
