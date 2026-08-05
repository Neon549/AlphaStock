"""Human-in-the-loop publication review endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agent_runtime.memory.long_term import LongTermMemory
from api.auth import verify_token
from api.review_store import get_review, resolve_review


router = APIRouter(tags=["reviews"])
memory = LongTermMemory()


class ReviewDecisionRequest(BaseModel):
    approved: bool


def _require_user(token: str) -> str:
    identity = verify_token(token)
    if not identity["valid"]:
        raise HTTPException(status_code=401, detail="valid X-Auth-Token is required")
    return identity["username"]


@router.get("/reviews/{review_id}")
def get_publication_review(review_id: str, x_auth_token: str = Header(...)):
    review = get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review not found")
    reviewer = _require_user(x_auth_token)
    if reviewer != review["payload"].get("requested_by"):
        raise HTTPException(status_code=403, detail="review belongs to another user")
    return review


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
    review = resolve_review(review_id, request.approved, reviewer)
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
        "decision": payload["draft_decision"] if request.approved else None,
    }
