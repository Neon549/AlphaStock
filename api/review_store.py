"""Small durable store for Human-in-the-Loop publication reviews."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DB_PATH = Path(__file__).resolve().parent.parent / "pending_reviews.db"


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS publication_reviews (
        review_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reviewer TEXT,
        reviewed_at TEXT,
        payload TEXT NOT NULL
        )"""
    )
    return conn


def create_review(payload: dict[str, Any]) -> str:
    review_id = str(uuid.uuid4())
    with _connection() as conn:
        conn.execute(
            "INSERT INTO publication_reviews VALUES (?, ?, ?, ?, ?, ?)",
            (review_id, "pending", datetime.now(timezone.utc).isoformat(), None, None,
             json.dumps(payload, ensure_ascii=False)),
        )
    return review_id


def get_review(review_id: str) -> dict[str, Any] | None:
    with _connection() as conn:
        row = conn.execute(
            "SELECT review_id, status, created_at, reviewer, reviewed_at, payload "
            "FROM publication_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
    if not row:
        return None
    return {
        "review_id": row[0], "status": row[1], "created_at": row[2],
        "reviewer": row[3], "reviewed_at": row[4], "payload": json.loads(row[5]),
    }


def resolve_review(review_id: str, approved: bool, reviewer: str) -> dict[str, Any] | None:
    review = get_review(review_id)
    if not review or review["status"] != "pending":
        return None
    status = "approved" if approved else "rejected"
    with _connection() as conn:
        conn.execute(
            "UPDATE publication_reviews SET status=?, reviewer=?, reviewed_at=? WHERE review_id=?",
            (status, reviewer, datetime.now(timezone.utc).isoformat(), review_id),
        )
    review["status"] = status
    review["reviewer"] = reviewer
    return review
