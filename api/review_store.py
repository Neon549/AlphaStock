"""Durable two-person publication review store.

The output gate validates the draft, an independent reviewer validates the
release decision, and the requesting user provides the final publication
confirmation.  This SQLite store deliberately keeps both actors and both
decisions so the audit trail does not collapse into one generic ``reviewer``
field.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_DB_PATH = Path(__file__).resolve().parent.parent / "pending_reviews.db"

PENDING_INDEPENDENT_REVIEW = "pending_independent_review"
PENDING_REQUESTER_CONFIRMATION = "pending_requester_confirmation"
REVIEWER_REJECTED = "reviewer_rejected"
APPROVED = "approved"
REJECTED = "rejected"


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Migrate the original six-column table without requiring a new file."""

    columns = {row[1] for row in conn.execute("PRAGMA table_info(publication_reviews)")}
    additions = {
        "independent_reviewer": "TEXT",
        "independent_reviewed_at": "TEXT",
        "independent_decision": "INTEGER",
        "independent_note": "TEXT",
        "final_reviewer": "TEXT",
        "final_reviewed_at": "TEXT",
        "final_note": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE publication_reviews ADD COLUMN {name} {definition}")

    # Existing pending records were awaiting a single user confirmation. They
    # must go through the independent stage after this migration.
    conn.execute(
        "UPDATE publication_reviews SET status=? WHERE status=?",
        (PENDING_INDEPENDENT_REVIEW, "pending"),
    )


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS publication_reviews (
        review_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reviewer TEXT,
        reviewed_at TEXT,
        payload TEXT NOT NULL,
        independent_reviewer TEXT,
        independent_reviewed_at TEXT,
        independent_decision INTEGER,
        independent_note TEXT,
        final_reviewer TEXT,
        final_reviewed_at TEXT,
        final_note TEXT
        )"""
    )
    _ensure_columns(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same_actor(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.strip().casefold() == right.strip().casefold())


def _row_to_review(row: tuple[Any, ...]) -> dict[str, Any]:
    independent_decision = row[8]
    return {
        "review_id": row[0],
        "status": row[1],
        "created_at": row[2],
        # ``reviewer`` and ``reviewed_at`` are retained as compatibility
        # aliases for the final requester decision.
        "reviewer": row[3],
        "reviewed_at": row[4],
        "payload": json.loads(row[5]),
        "independent_reviewer": row[6],
        "independent_reviewed_at": row[7],
        "independent_decision": None if independent_decision is None else bool(independent_decision),
        "independent_note": row[9] or "",
        "final_reviewer": row[10] or row[3],
        "final_reviewed_at": row[11] or row[4],
        "final_note": row[12] or "",
    }


def _select_review(conn: sqlite3.Connection, review_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT review_id, status, created_at, reviewer, reviewed_at, payload,
                  independent_reviewer, independent_reviewed_at,
                  independent_decision, independent_note, final_reviewer,
                  final_reviewed_at, final_note
           FROM publication_reviews WHERE review_id = ?""",
        (review_id,),
    ).fetchone()
    return _row_to_review(row) if row else None


def create_review(payload: dict[str, Any]) -> str:
    """Create a publication draft that cannot be finalized directly."""

    review_id = str(uuid.uuid4())
    with _connection() as conn:
        conn.execute(
            """INSERT INTO publication_reviews
               (review_id, status, created_at, reviewer, reviewed_at, payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (review_id, PENDING_INDEPENDENT_REVIEW, _now(), None, None,
             json.dumps(payload, ensure_ascii=False)),
        )
    return review_id


def get_review(review_id: str) -> dict[str, Any] | None:
    with _connection() as conn:
        return _select_review(conn, review_id)


def independent_reviewer_users() -> frozenset[str]:
    """Return the configured independent publication reviewers.

    Keeping this as an explicit allowlist makes the default fail closed. A
    production deployment can later replace it with a directory/role lookup
    without changing the review state machine.
    """

    raw = os.getenv("PUBLICATION_REVIEWER_USERS", "")
    return frozenset(item.strip().casefold() for item in raw.split(",") if item.strip())


def is_independent_reviewer(username: str) -> bool:
    return username.strip().casefold() in independent_reviewer_users()


def resolve_independent_review(
    review_id: str,
    approved: bool,
    reviewer: str,
    note: str = "",
) -> dict[str, Any] | None:
    """Resolve the independent reviewer stage and advance the state."""

    review = get_review(review_id)
    if not review or review["status"] != PENDING_INDEPENDENT_REVIEW:
        return None
    requested_by = review["payload"].get("requested_by")
    if _same_actor(reviewer, requested_by):
        raise PermissionError("independent reviewer must differ from requester")

    status = PENDING_REQUESTER_CONFIRMATION if approved else REVIEWER_REJECTED
    reviewed_at = _now()
    with _connection() as conn:
        updated = conn.execute(
            """UPDATE publication_reviews
               SET status=?, independent_reviewer=?, independent_reviewed_at=?,
                   independent_decision=?, independent_note=?
               WHERE review_id=? AND status=?""",
            (status, reviewer, reviewed_at, int(approved), note.strip()[:2_000],
             review_id, PENDING_INDEPENDENT_REVIEW),
        ).rowcount
        if updated != 1:
            return None
        return _select_review(conn, review_id)


def resolve_review(
    review_id: str,
    approved: bool,
    reviewer: str,
    note: str = "",
) -> dict[str, Any] | None:
    """Record the requester's final confirmation after reviewer approval."""

    review = get_review(review_id)
    if not review or review["status"] != PENDING_REQUESTER_CONFIRMATION:
        return None
    requested_by = review["payload"].get("requested_by")
    if requested_by and not _same_actor(reviewer, requested_by):
        raise PermissionError("final confirmation must be made by requester")
    if _same_actor(reviewer, review.get("independent_reviewer")):
        raise PermissionError("independent reviewer cannot provide final confirmation")

    status = APPROVED if approved else REJECTED
    reviewed_at = _now()
    with _connection() as conn:
        updated = conn.execute(
            """UPDATE publication_reviews
               SET status=?, reviewer=?, reviewed_at=?, final_reviewer=?,
                   final_reviewed_at=?, final_note=?
               WHERE review_id=? AND status=?""",
            (status, reviewer, reviewed_at, reviewer, reviewed_at,
             note.strip()[:2_000], review_id, PENDING_REQUESTER_CONFIRMATION),
        ).rowcount
        if updated != 1:
            return None
        return _select_review(conn, review_id)
