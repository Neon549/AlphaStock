"""Explicit user preferences and human-reviewed long-term memory candidates."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agent_runtime.memory.candidates import create_candidate, get_candidate, list_candidates, review_candidate
from agent_runtime.memory.manager import PostgresMemoryManager
from agent_runtime.memory.reflection import candidate_from_backtest_deviation, candidate_from_bad_case
from api.auth import verify_token


router = APIRouter(tags=["memory"])
memory_manager = PostgresMemoryManager()


class UserPreferencesRequest(BaseModel):
    risk_profile: Optional[str] = None
    preferred_sectors: Optional[list[str]] = None
    answer_style: Optional[str] = None
    watchlist: Optional[list[str]] = None


class MemoryCandidateRequest(BaseModel):
    title: str
    content: str
    category: str = "governance"
    source_run_id: Optional[str] = None


class MemoryCandidateDecisionRequest(BaseModel):
    approved: bool
    review_note: str = ""


class BadCaseCandidateRequest(BaseModel):
    failure_type: str
    observed: str
    expected: str
    root_cause: str = "pending human review"
    source_run_id: Optional[str] = None


class BacktestDeviationCandidateRequest(BaseModel):
    expected: dict
    actual: dict
    source_run_id: Optional[str] = None


def _require_user(token: str) -> str:
    identity = verify_token(token)
    if not identity["valid"]:
        raise HTTPException(status_code=401, detail="valid X-Auth-Token is required")
    return identity["username"]


@router.get("/memory/preferences")
def get_user_preferences(x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    return {"actor_id": actor_id, "preferences": memory_manager.get_preferences(actor_id)}


@router.put("/memory/preferences")
def put_user_preferences(request: UserPreferencesRequest, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    payload = request.model_dump(exclude_none=True) if hasattr(request, "model_dump") else request.dict(exclude_none=True)
    return {"actor_id": actor_id, "preferences": memory_manager.set_preferences(actor_id, payload)}


@router.get("/memory/candidates")
def get_memory_candidates(status: str = "pending", x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    try:
        return {"candidates": list_candidates(requested_by=actor_id, status=status)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/memory/candidates")
def create_memory_candidate(request: MemoryCandidateRequest, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    try:
        candidate = create_candidate(
            title=request.title,
            content=request.content,
            category=request.category,
            source_run_id=request.source_run_id,
            requested_by=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidate_id": candidate.candidate_id, "status": "pending"}


@router.post("/memory/candidates/bad-case")
def create_bad_case_candidate(request: BadCaseCandidateRequest, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    try:
        candidate = candidate_from_bad_case(
            request.model_dump() if hasattr(request, "model_dump") else request.dict(),
            requested_by=actor_id,
            source_run_id=request.source_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidate_id": candidate.candidate_id, "status": "pending"}


@router.post("/memory/candidates/backtest-deviation")
def create_backtest_deviation_candidate(
    request: BacktestDeviationCandidateRequest, x_auth_token: str = Header(...)
):
    actor_id = _require_user(x_auth_token)
    try:
        candidate = candidate_from_backtest_deviation(
            expected=request.expected,
            actual=request.actual,
            requested_by=actor_id,
            source_run_id=request.source_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidate_id": candidate.candidate_id, "status": "pending"}


@router.get("/memory/candidates/{candidate_id}")
def get_memory_candidate(candidate_id: str, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="memory candidate not found")
    if candidate.get("requested_by") != actor_id:
        raise HTTPException(status_code=403, detail="candidate belongs to another user")
    return candidate


@router.post("/memory/candidates/{candidate_id}/decision")
def decide_memory_candidate(
    candidate_id: str,
    request: MemoryCandidateDecisionRequest,
    x_auth_token: str = Header(...),
):
    reviewer = _require_user(x_auth_token)
    existing = get_candidate(candidate_id)
    if not existing:
        raise HTTPException(status_code=404, detail="memory candidate not found")
    if existing.get("requested_by") != reviewer:
        raise HTTPException(status_code=403, detail="candidate belongs to another user")
    resolved = review_candidate(
        candidate_id,
        approved=request.approved,
        reviewer=reviewer,
        review_note=request.review_note,
    )
    if not resolved:
        raise HTTPException(status_code=409, detail="candidate was already resolved")
    return {
        "candidate_id": candidate_id,
        "status": resolved["status"],
        "approved_path": resolved.get("approved_path"),
        "index_sync_required": bool(request.approved),
    }
