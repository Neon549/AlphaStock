"""Research ingress: chat, stock analysis, webhook dispatch and evidence history."""

from __future__ import annotations

import os
from hashlib import sha256
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agent_runtime.memory.long_term import LongTermMemory
from agent_runtime.memory.manager import PostgresMemoryManager
from agent_runtime.skills.registry import skill_registry
from api.auth import verify_token
from api.review_store import create_review
from api.security import claim_session, extract_auth_token, require_actor
from control_plane.contracts import AgentEvent, TriggerType
from control_plane.gateway import Gateway
from control_plane.investment_runtime import InvestmentRuntime
from control_plane.run_store import PostgresRunStore
from control_plane.security import SecurityOperation, authorize_operation


router = APIRouter(tags=["research"])
memory = LongTermMemory()
runtime_memory = PostgresMemoryManager()
investment_gateway = Gateway(InvestmentRuntime(memory_manager=runtime_memory), store=PostgresRunStore())


class AnalyzeRequest(BaseModel):
    stock_code: str
    force_refresh: bool = False
    model: str = "smart"
    session_id: Optional[str] = None
    auth_token: Optional[str] = None
    learning_capture: bool = False


class AnalyzeResponse(BaseModel):
    run_id: str
    stock_code: str
    decision: str
    fundamental_report: str
    technical_report: str
    sentiment_report: str
    researcher_analysis: str
    status: str = "success"
    publish_status: str = "requires_human_review"
    review_id: Optional[str] = None
    publish_reasons: list[str] = []
    document_citations: list[dict] = []
    evidence_cards: list[dict] = []
    trace_summary: dict = {}


class ChatRequest(BaseModel):
    message: str
    model: str = "smart"
    session_id: Optional[str] = None
    auth_token: Optional[str] = None
    learning_capture: bool = False


class WebhookRequest(BaseModel):
    content: str
    session_id: Optional[str] = None
    actor_id: Optional[str] = None
    channel: str = "webhook"
    event_id: Optional[str] = None
    model: str = "smart"
    type: str = "generic"


def _event_id(operation: str, session_id: str | None, actor_id: str, key: str | None) -> str | None:
    if not key or not key.strip():
        return None
    raw = f"{operation}:{actor_id}:{session_id or 'no-session'}:{key.strip()}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _authorize(tool: str, target: str, actor_id: str, session_id: str | None = None) -> None:
    try:
        authorize_operation(
            SecurityOperation(tool=tool, target=target, actor_id=actor_id, session_id=session_id),
            mode="auto",
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="operation is not permitted") from exc


def _create_publication_review(result: dict, auth_token: str | None) -> str | None:
    if result.get("publish_status") != "requires_human_review":
        return None
    identity = verify_token(auth_token or "")
    if not identity["valid"]:
        result["publish_status"] = "blocked"
        result["human_review_required"] = True
        result["publish_reasons"] = ["an authenticated user is required to submit a draft for review"]
        return None
    return create_review(
        {
            "requested_by": identity["username"],
            "stock_code": result.get("stock_code"),
            "draft_decision": result.get("draft_decision") or result.get("final_decision"),
            "fundamental_report": result.get("fundamental_report", ""),
            "technical_report": result.get("technical_report", ""),
            "sentiment_report": result.get("sentiment_report", ""),
            "publish_reasons": result.get("publish_reasons", []),
        }
    )


@router.get("/health")
def health_check():
    return {"status": "ok", "message": "Trading Agent System is running"}


@router.post("/webhooks/agent")
def agent_webhook(request: WebhookRequest, x_webhook_signature: str = Header(...)):
    from control_plane.triggers import webhook_event

    payload = request.model_dump(exclude_none=True) if hasattr(request, "model_dump") else request.dict(exclude_none=True)
    try:
        event = webhook_event(payload, x_webhook_signature, os.getenv("AGENT_WEBHOOK_SECRET", ""))
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    run = investment_gateway.dispatch(event)
    if run.route == "duplicate":
        raise HTTPException(status_code=409, detail="duplicate webhook event")
    return {"run_id": run.run_id, "route": run.route, "status": run.payload.get("status")}


@router.get("/skills")
def list_skills():
    return {"skills": skill_registry.list_public()}


@router.post("/chat")
def chat(
    request: ChatRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    query = request.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="消息不能为空")
    actor_id = require_actor(
        body_token=request.auth_token, x_auth_token=x_auth_token, authorization=authorization
    )
    if request.session_id:
        request.session_id = claim_session(request.session_id, actor_id)
    _authorize("agent", "chat", actor_id, request.session_id)
    event_kwargs = {
        "trigger": TriggerType.MESSAGE,
        "content": query,
        "session_id": request.session_id,
        "actor_id": actor_id,
        "channel": "web",
        "metadata": {
            "model": request.model or "smart",
            "learning_capture": bool(request.learning_capture),
        },
    }
    event_id = _event_id("chat", request.session_id, actor_id, idempotency_key)
    if event_id:
        event_kwargs["event_id"] = event_id
    run = investment_gateway.dispatch(AgentEvent(**event_kwargs))
    if run.route == "duplicate":
        raise HTTPException(status_code=409, detail="duplicate request is already recorded; retry without reusing the key after checking run status")
    payload = dict(run.payload)
    payload.pop("_run_telemetry", None)
    workflow_result = payload.pop("workflow_result", None)
    if workflow_result is not None:
        payload["review_id"] = _create_publication_review(
            workflow_result,
            extract_auth_token(
                body_token=request.auth_token, x_auth_token=x_auth_token, authorization=authorization
            ),
        )
    # Chat exposes a stable, privacy-safe RAG envelope.  Detailed tool payloads,
    # provider attempts and raw telemetry remain in the private audit store.
    return {
        "run_id": run.run_id,
        "answer": payload.get("content") or payload.get("decision", ""),
        "citations": payload.get("document_citations", []),
        "publish_status": payload.get("publish_status", "success"),
        "publish_reasons": payload.get("publish_reasons", []),
        "human_review_required": payload.get("human_review_required", False),
        "evidence_cards": payload.get("evidence_cards", []),
        "trace_summary": payload.get("trace_summary", {}),
    }


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_stock(
    request: AnalyzeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    stock_code = request.stock_code.strip()
    if not stock_code:
        raise HTTPException(status_code=400, detail="股票代码不能为空")
    try:
        actor_id = require_actor(
            body_token=request.auth_token, x_auth_token=x_auth_token, authorization=authorization
        )
        if request.session_id:
            request.session_id = claim_session(request.session_id, actor_id)
        _authorize("agent", "analyze", actor_id, request.session_id)
        event_kwargs = {
            "trigger": TriggerType.HTTP,
            "content": f"分析 {stock_code}",
            "session_id": request.session_id,
            "actor_id": actor_id,
            "channel": "web",
            "metadata": {
                "operation": "analyze",
                "model": request.model or "smart",
                "learning_capture": bool(request.learning_capture),
            },
        }
        event_id = _event_id("analyze", request.session_id, actor_id, idempotency_key)
        if event_id:
            event_kwargs["event_id"] = event_id
        run = investment_gateway.dispatch(AgentEvent(**event_kwargs))
        if run.route == "duplicate":
            raise HTTPException(status_code=409, detail="duplicate request is already recorded; retry without reusing the key after checking run status")
        payload = dict(run.payload)
        payload.pop("_run_telemetry", None)
        workflow_result = payload.pop("workflow_result", None)
        if workflow_result is None:
            raise HTTPException(status_code=400, detail=payload.get("content", "无法启动股票分析"))
        review_id = _create_publication_review(
            workflow_result,
            extract_auth_token(
                body_token=request.auth_token, x_auth_token=x_auth_token, authorization=authorization
            ),
        )
        return AnalyzeResponse(
            run_id=run.run_id,
            stock_code=stock_code,
            decision=payload.get("decision", ""),
            fundamental_report=payload.get("fundamental_report", ""),
            technical_report=payload.get("technical_report", ""),
            sentiment_report=payload.get("sentiment_report", ""),
            researcher_analysis=payload.get("researcher_analysis", ""),
            status=payload.get("status", "blocked"),
            publish_status=payload.get("publish_status", "blocked"),
            publish_reasons=payload.get("publish_reasons", []),
            review_id=review_id,
            document_citations=payload.get("document_citations", []),
            evidence_cards=payload.get("evidence_cards", []),
            trace_summary=payload.get("trace_summary", {}),
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="analysis failed")


@router.get("/history/{stock_code}")
def get_history(stock_code: str):
    return {"stock_code": stock_code, "history": memory.get_history(stock_code)}


@router.get("/stocks/info/{stock_code}")
def get_stock_info(stock_code: str):
    try:
        from tools.akshare_tools import get_stock_price
        from market.evidence import build_market_evidence_record, persist_market_evidence_record

        info = get_stock_price.invoke({"symbol": stock_code})
        record = build_market_evidence_record("market-price", stock_code, str(info))
        evidence_id = persist_market_evidence_record(record) if record else None
        return {"stock_code": stock_code, "info": info, "evidence_id": evidence_id}
    except Exception:
        raise HTTPException(status_code=500, detail="stock info failed")


@router.get("/stocks/evidence/{stock_code}")
def get_market_evidence(stock_code: str, evidence_type: str | None = None, limit: int = 20):
    """Return structured market/financial snapshots without raw prompt text."""

    allowed_types = {"quote", "financial_indicator", "daily_history"}
    if evidence_type and evidence_type not in allowed_types:
        raise HTTPException(status_code=400, detail="unsupported evidence_type")
    try:
        from market.evidence import get_latest_market_evidence

        return {
            "stock_code": stock_code,
            "evidence_type": evidence_type,
            "items": get_latest_market_evidence(
                stock_code,
                evidence_type=evidence_type,
                limit=limit,
            ),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=503, detail="market evidence unavailable")
