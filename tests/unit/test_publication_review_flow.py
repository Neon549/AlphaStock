import sqlite3

import pytest
from fastapi import HTTPException

from api import review_store
from api.routers import reviews as reviews_router


def _payload(requested_by: str = "alice") -> dict:
    return {
        "requested_by": requested_by,
        "stock_code": "600000",
        "draft_decision": "观察",
    }


def test_publication_review_requires_two_distinct_actors(monkeypatch, tmp_path):
    monkeypatch.setattr(review_store, "_DB_PATH", tmp_path / "pending_reviews.db")
    review_id = review_store.create_review(_payload())

    created = review_store.get_review(review_id)
    assert created["status"] == review_store.PENDING_INDEPENDENT_REVIEW

    with pytest.raises(PermissionError):
        review_store.resolve_independent_review(review_id, True, "alice")

    reviewer_step = review_store.resolve_independent_review(
        review_id, True, "bob", "证据、风险和引用均已复核"
    )
    assert reviewer_step["status"] == review_store.PENDING_REQUESTER_CONFIRMATION
    assert reviewer_step["independent_reviewer"] == "bob"
    assert reviewer_step["independent_decision"] is True

    with pytest.raises(PermissionError):
        review_store.resolve_review(review_id, True, "bob")

    final = review_store.resolve_review(review_id, True, "alice", "确认发布")
    assert final["status"] == review_store.APPROVED
    assert final["final_reviewer"] == "alice"
    assert final["independent_reviewer"] == "bob"


def test_legacy_pending_review_is_migrated_to_independent_stage(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(review_store, "_DB_PATH", db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE publication_reviews (
               review_id TEXT PRIMARY KEY, status TEXT NOT NULL,
               created_at TEXT NOT NULL, reviewer TEXT,
               reviewed_at TEXT, payload TEXT NOT NULL)"""
        )
        conn.execute(
            "INSERT INTO publication_reviews VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy", "pending", "2026-08-15T00:00:00+00:00", None, None, "{}"),
        )

    migrated = review_store.get_review("legacy")
    assert migrated["status"] == review_store.PENDING_INDEPENDENT_REVIEW
    assert migrated["independent_reviewer"] is None


def test_publication_routes_enforce_reviewer_allowlist_and_final_confirmation(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(review_store, "_DB_PATH", tmp_path / "pending_reviews.db")
    monkeypatch.setenv("PUBLICATION_REVIEWER_USERS", "bob")
    monkeypatch.setattr(
        reviews_router,
        "verify_token",
        lambda token: {"valid": True, "username": token},
    )
    monkeypatch.setattr(reviews_router.memory, "save_decision", lambda **_: None)
    review_id = review_store.create_review(_payload())

    with pytest.raises(HTTPException) as requester_error:
        reviews_router.decide_independent_publication_review(
            review_id,
            reviews_router.ReviewDecisionRequest(approved=True),
            "alice",
        )
    assert requester_error.value.status_code == 403

    independent = reviews_router.decide_independent_publication_review(
        review_id,
        reviews_router.ReviewDecisionRequest(approved=True, note="独立复核通过"),
        "bob",
    )
    assert independent["publish_status"] == review_store.PENDING_REQUESTER_CONFIRMATION

    final = reviews_router.decide_publication_review(
        review_id,
        reviews_router.ReviewDecisionRequest(approved=True),
        "alice",
    )
    assert final["publish_status"] == review_store.APPROVED
