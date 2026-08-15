"""Explicit user preferences and human-reviewed long-term memory candidates."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agent_runtime.governance.approval_modes import (
    FULL_ACCESS,
    ApprovalModeConfirmationRequired,
    classify_memory_candidate,
    get_approval_mode,
    route_memory_candidate,
    set_approval_mode,
)
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


class MemoryCandidateBatchDecisionRequest(BaseModel):
    candidate_ids: list[str]
    approved: bool
    review_note: str = ""


class ApprovalModeRequest(BaseModel):
    mode: str
    confirm_risk: bool = False
    ttl_minutes: Optional[int] = None


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


def _candidate_response(candidate, actor_id: str) -> dict:
    stored = get_candidate(candidate.candidate_id) or {}
    mode = get_approval_mode(actor_id)
    risk = classify_memory_candidate(
        category=candidate.category, title=candidate.title, content=candidate.content
    )
    return {
        "candidate_id": candidate.candidate_id,
        "status": stored.get("status", "pending"),
        "approval_mode": mode["mode"],
        "risk_level": risk,
        "review_action": route_memory_candidate(mode["mode"], risk),
        "index_sync_required": stored.get("status") == "approved",
    }


@router.get("/memory/preferences")
def get_user_preferences(x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    return {"actor_id": actor_id, "preferences": memory_manager.get_preferences(actor_id)}


@router.put("/memory/preferences")
def put_user_preferences(request: UserPreferencesRequest, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    payload = request.model_dump(exclude_none=True) if hasattr(request, "model_dump") else request.dict(exclude_none=True)
    return {"actor_id": actor_id, "preferences": memory_manager.set_preferences(actor_id, payload)}


@router.get("/memory/approval-mode")
def get_user_approval_mode(x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    return get_approval_mode(actor_id)


@router.put("/memory/approval-mode")
def put_user_approval_mode(request: ApprovalModeRequest, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    try:
        return set_approval_mode(
            actor_id,
            request.mode,
            confirm_risk=request.confirm_risk,
            ttl_minutes=request.ttl_minutes,
        )
    except ApprovalModeConfirmationRequired as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "elevated_confirmation_required",
                "mode": FULL_ACCESS,
                "message": str(exc),
                "risk_summary": [
                    "完全访问权限只对低/中风险长期记忆自动处理",
                    "高风险候选仍需批量确认",
                    "实时行情、当前财务事实、投资推荐、隐私和密钥始终被硬阻断",
                ],
                "requires_explicit_confirmation": True,
            },
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/memory/candidates")
def get_memory_candidates(status: str = "pending", x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    try:
        candidates = list_candidates(requested_by=actor_id, status=status)
        mode = get_approval_mode(actor_id)
        for candidate in candidates:
            risk = classify_memory_candidate(
                category=candidate["category"],
                title=candidate["title"],
                content=candidate["content"],
            )
            candidate.update(
                approval_mode=mode["mode"],
                risk_level=risk,
                review_action=route_memory_candidate(mode["mode"], risk),
            )
        return {"candidates": candidates, "approval_mode": mode}
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
    return _candidate_response(candidate, actor_id)


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
    return _candidate_response(candidate, actor_id)


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
    return _candidate_response(candidate, actor_id)


@router.get("/memory/candidates/{candidate_id}")
def get_memory_candidate(candidate_id: str, x_auth_token: str = Header(...)):
    actor_id = _require_user(x_auth_token)
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="memory candidate not found")
    if candidate.get("requested_by") != actor_id:
        raise HTTPException(status_code=403, detail="candidate belongs to another user")
    mode = get_approval_mode(actor_id)
    risk = classify_memory_candidate(
        category=candidate["category"], title=candidate["title"], content=candidate["content"]
    )
    candidate.update(
        approval_mode=mode["mode"],
        risk_level=risk,
        review_action=route_memory_candidate(mode["mode"], risk),
    )
    return candidate


@router.post("/memory/candidates/batch-decision")
def decide_memory_candidates_batch(
    request: MemoryCandidateBatchDecisionRequest,
    x_auth_token: str = Header(...),
):
    """Resolve a group with one user confirmation instead of one per item."""

    reviewer = _require_user(x_auth_token)
    candidate_ids = list(dict.fromkeys(request.candidate_ids))
    if not candidate_ids or len(candidate_ids) > 100:
        raise HTTPException(status_code=400, detail="candidate_ids must contain 1-100 items")

    results = []
    for candidate_id in candidate_ids:
        existing = get_candidate(candidate_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"memory candidate not found: {candidate_id}")
        if existing.get("requested_by") != reviewer:
            raise HTTPException(status_code=403, detail="candidate belongs to another user")
        resolved = review_candidate(
            candidate_id,
            approved=request.approved,
            reviewer=reviewer,
            review_note=request.review_note,
        )
        results.append({
            "candidate_id": candidate_id,
            "status": resolved["status"] if resolved else existing.get("status"),
            "approved_path": resolved.get("approved_path") if resolved else existing.get("approved_path"),
            "index_sync_required": bool(resolved and resolved["status"] == "approved"),
        })
    return {
        "reviewer": reviewer,
        "count": len(results),
        "results": results,
        "single_confirmation": True,
    }


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
