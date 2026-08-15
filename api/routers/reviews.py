"""Two-person publication review endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agent_runtime.memory.long_term import LongTermMemory
from api.auth import verify_token
from api.review_store import (
    PENDING_INDEPENDENT_REVIEW,
    PENDING_REQUESTER_CONFIRMATION,
    get_review,
    is_independent_reviewer,
    resolve_independent_review,
    resolve_review,
)


router = APIRouter(tags=["reviews"])
memory = LongTermMemory()


class ReviewDecisionRequest(BaseModel):
    approved: bool
    note: str = ""


def _require_user(token: str) -> str:
    identity = verify_token(token)
    if not identity["valid"]:
        raise HTTPException(status_code=401, detail="valid X-Auth-Token is required")
    return identity["username"]


def _require_independent_reviewer(token: str, requested_by: str | None) -> str:
    reviewer = _require_user(token)
    if not is_independent_reviewer(reviewer):
        raise HTTPException(
            status_code=403,
            detail="independent publication reviewer role is required",
        )
    if requested_by and reviewer.casefold() == requested_by.casefold():
        raise HTTPException(
            status_code=403,
            detail="independent reviewer must differ from requester",
        )
    return reviewer


@router.get("/reviews/{review_id}")
def get_publication_review(review_id: str, x_auth_token: str = Header(...)):
    review = get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review not found")
    reviewer = _require_user(x_auth_token)
    requested_by = review["payload"].get("requested_by")
    can_view_as_requester = reviewer == requested_by
    can_view_as_independent_reviewer = (
        is_independent_reviewer(reviewer)
        and not (requested_by and reviewer.casefold() == requested_by.casefold())
    )
    if not can_view_as_requester and not can_view_as_independent_reviewer:
        raise HTTPException(status_code=403, detail="review belongs to another user")
    return review


@router.post("/reviews/{review_id}/reviewer-decision")
def decide_independent_publication_review(
    review_id: str,
    request: ReviewDecisionRequest,
    x_auth_token: str = Header(...),
):
    """Resolve the independent reviewer stage before requester confirmation."""

    existing = get_review(review_id)
    if not existing:
        raise HTTPException(status_code=404, detail="review not found")
    reviewer = _require_independent_reviewer(
        x_auth_token, existing["payload"].get("requested_by")
    )
    try:
        review = resolve_independent_review(
            review_id, request.approved, reviewer, request.note
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not review:
        raise HTTPException(status_code=409, detail="review is missing or already resolved")
    return {
        "review_id": review_id,
        "publish_status": review["status"],
        "reviewer": reviewer,
        "reviewer_role": "independent",
        "note": review["independent_note"],
    }


@router.post("/reviews/{review_id}/decision")
def decide_publication_review(
    review_id: str,
    request: ReviewDecisionRequest,
    x_auth_token: str = Header(...),
):
    reviewer = _require_user(x_auth_token)
    existing = get_review(review_id)
    if not existing:
        raise HTTPException(status_code=404, detail="review not found")
    if reviewer != existing["payload"].get("requested_by"):
        raise HTTPException(status_code=403, detail="review belongs to another user")
    if existing["status"] == PENDING_INDEPENDENT_REVIEW:
        raise HTTPException(
            status_code=409,
            detail="independent reviewer approval is required before final confirmation",
        )
    if existing["status"] != PENDING_REQUESTER_CONFIRMATION:
        raise HTTPException(status_code=409, detail="review is missing or already resolved")
    try:
        review = resolve_review(review_id, request.approved, reviewer, request.note)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not review:
        raise HTTPException(status_code=409, detail="review is missing or already resolved")
    payload = review["payload"]
    if request.approved:
        memory.save_decision(
            stock_code=payload["stock_code"],
            decision=payload["draft_decision"],
            fundamental_summary=payload.get("fundamental_report", "")[:300],
            technical_summary=payload.get("technical_report", "")[:300],
            sentiment_summary=payload.get("sentiment_report", "")[:300],
        )
    return {
        "review_id": review_id,
        "publish_status": review["status"],
        "reviewer": reviewer,
        "reviewer_role": "requester_final_confirmation",
        "independent_reviewer": review["independent_reviewer"],
        "decision": payload["draft_decision"] if request.approved else None,
    }
