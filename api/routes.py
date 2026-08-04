# api/routes.py
# ============ 改动说明 ============
# 新增: POST /api/v1/backtest 回测接口
# 新增: BacktestRequest / BacktestResponse 模型
# 新增: POST /api/v1/chat 自然语言对话接口（含意图识别）
# 原有接口不变
# ==================================

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form, Header
from pydantic import BaseModel
from typing import Optional
from hashlib import sha256
import os
from agent_runtime.memory.long_term import LongTermMemory
from control_plane.contracts import AgentEvent, TriggerType
from control_plane.gateway import Gateway
from control_plane.investment_runtime import InvestmentRuntime
from control_plane.run_store import PostgresRunStore
from agent_runtime.memory.manager import PostgresMemoryManager
from agent_runtime.memory.candidates import create_candidate, get_candidate, list_candidates, review_candidate
from agent_runtime.memory.reflection import candidate_from_bad_case, candidate_from_backtest_deviation
from api.multimodal import (
    analyze_image,
    cleanup_document,
    index_image_analysis,
    process_document,
    cleanup_session,
)
from api.review_store import create_review, get_review, resolve_review
from api.auth import verify_token
from agent_runtime.skills.registry import skill_registry

router = APIRouter()
memory = LongTermMemory()
runtime_memory = PostgresMemoryManager()
investment_gateway = Gateway(InvestmentRuntime(memory_manager=runtime_memory), store=PostgresRunStore())


def _event_id_from_request(
    operation: str, session_id: str | None, actor_id: str | None, idempotency_key: str | None
) -> str | None:
    """Namespace a client retry key before it becomes the global event primary key."""
    if not idempotency_key or not idempotency_key.strip():
        return None
    raw = f"{operation}:{actor_id or 'anonymous'}:{session_id or 'no-session'}:{idempotency_key.strip()}"
    return sha256(raw.encode("utf-8")).hexdigest()


def _optional_actor(auth_token: str | None) -> str | None:
    identity = verify_token(auth_token or "")
    return identity["username"] if identity.get("valid") else None


def _apply_model_config(model: str):
    """
    根据前端选择的模型，动态切换后端LLM配置
    fast:   DeepSeek-V3（快速便宜，适合选股筛选）
    smart:  DeepSeek-R1（推理强，适合深度分析）默认
    strong: DeepSeek-R1 + 更低temperature（严格，适合量化回测）
    """
    # Kept as a no-op compatibility shim for existing route call sites.
    # The name is carried in AgentEvent.metadata and resolved per run.
    return model if model in {"fast", "smart", "strong"} else "smart"

    import config.llm_config as llm_cfg
    from config.llm_config import FallbackLLM, _make_deepseek, _qwen_backup

    if model == "fast":
        # 用V3，快速便宜
        llm_cfg.deep_llm = FallbackLLM(
            primary=_make_deepseek("deepseek-chat", temperature=0.1),
            backup=_qwen_backup,
            name="DeepLLM[fast]",
        )
        llm_cfg.quick_llm = FallbackLLM(
            primary=_make_deepseek("deepseek-chat", temperature=0.1),
            backup=_qwen_backup,
            name="QuickLLM[fast]",
        )
        print("[ModelConfig] 切换到 Fast 模式（DeepSeek-V3）")

    elif model == "strong":
        # R1 + 更低temperature，更严格
        llm_cfg.deep_llm = FallbackLLM(
            primary=_make_deepseek("deepseek-reasoner", temperature=0.0),
            backup=_make_deepseek("deepseek-chat", temperature=0.0),
            name="DeepLLM[strong]",
        )
        llm_cfg.quick_llm = FallbackLLM(
            primary=_make_deepseek("deepseek-reasoner", temperature=0.0),
            backup=_make_deepseek("deepseek-chat", temperature=0.0),
            name="QuickLLM[strong]",
        )
        print("[ModelConfig] 切换到 Strong 模式（DeepSeek-R1, temp=0）")

    else:
        # smart（默认），R1推理 + V3快速
        llm_cfg.deep_llm = FallbackLLM(
            primary=_make_deepseek("deepseek-reasoner", temperature=0.1),
            backup=_make_deepseek("deepseek-chat", temperature=0.1),
            name="DeepLLM[smart]",
        )
        llm_cfg.quick_llm = FallbackLLM(
            primary=_make_deepseek("deepseek-chat", temperature=0.1),
            backup=_qwen_backup,
            name="QuickLLM[smart]",
        )
        print("[ModelConfig] 切换到 Smart 模式（DeepSeek-R1）")


# ── 原有模型 ────────────────────────────────


class AnalyzeRequest(BaseModel):
    stock_code: str
    force_refresh: bool = False
    model: str = "smart"  # fast / smart / strong
    session_id: Optional[str] = None  # 用于关联上传的文档
    auth_token: Optional[str] = None  # required to create a publish review


class AnalyzeResponse(BaseModel):
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


class HistoryResponse(BaseModel):
    stock_code: str
    history: str


# ── 新增：回测模型 ──────────────────────────


class BacktestRequest(BaseModel):
    stock_code: str
    strategy: str = "kdj_macd"  # kdj_macd / rsi / boll
    start_date: str = "20220101"
    end_date: str = "20261231"
    initial_cash: float = 100000.0


class BacktestResponse(BaseModel):
    stock_code: str
    strategy: str
    total_return: float
    sharpe: Optional[float]
    max_drawdown: float
    trade_count: int
    win_rate: float
    report_text: str
    report_path: Optional[str] = None
    returns_data: Optional[list] = None
    dates_data: Optional[list] = None
    trade_records: Optional[list] = None
    status: str = "success"


# ── 新增：自然语言对话模型 ──────────────────


class ChatRequest(BaseModel):
    message: str
    model: str = "smart"  # fast / smart / strong
    session_id: Optional[str] = None
    auth_token: Optional[str] = None


class ReviewDecisionRequest(BaseModel):
    approved: bool


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


class WebhookRequest(BaseModel):
    content: str
    session_id: Optional[str] = None
    actor_id: Optional[str] = None
    channel: str = "webhook"
    event_id: Optional[str] = None
    model: str = "smart"
    type: str = "generic"


def _create_publication_review(result: dict, auth_token: Optional[str]) -> Optional[str]:
    """Persist a reviewable draft; it is not published or memorised yet."""
    if result.get("publish_status") != "requires_human_review":
        return None
    identity = verify_token(auth_token or "")
    if not identity["valid"]:
        result["publish_status"] = "blocked"
        result["human_review_required"] = True
        result["publish_reasons"] = ["an authenticated user is required to submit a draft for review"]
        return None
    return create_review({
        "requested_by": identity["username"],
        "stock_code": result.get("stock_code"),
        "draft_decision": result.get("draft_decision") or result.get("final_decision"),
        "fundamental_report": result.get("fundamental_report", ""),
        "technical_report": result.get("technical_report", ""),
        "sentiment_report": result.get("sentiment_report", ""),
        "publish_reasons": result.get("publish_reasons", []),
    })


# ── 原有接口 ────────────────────────────────


@router.get("/health")
def health_check():
    return {"status": "ok", "message": "Trading Agent System is running"}


@router.post("/webhooks/agent")
def agent_webhook(request: WebhookRequest, x_webhook_signature: str = Header(...)):
    """Signed external ingress; verification happens before Gateway dispatch."""
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
    """Return registry metadata plus an immutable content fingerprint per version."""
    return {"skills": skill_registry.list_public()}


# ── 新增：自然语言对话接口 ──────────────────


@router.post("/chat")
def chat(request: ChatRequest, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    """
    自然语言对话入口，含意图识别。
    用户无需输入股票代码，直接说"帮我分析宁德时代"即可。

    路由逻辑：
      意图1（开放性讨论）→ quick_llm 直接回答，不启动 Agent
      意图2（操作性分析）→ 完整 LangGraph 流程，支持 analyst_focus 按需启动 Analyst
      意图3（系统功能）  → 提示用户使用对应功能入口
      意图4（信息不足）  → 追问用户
    """
    query = request.message.strip()
    if not query:
        raise HTTPException(status_code=400, detail="消息不能为空")

    # FastAPI only adapts HTTP to an internal event. The Gateway does not call
    # models or tools; InvestmentRuntime owns the route and execution boundary.
    _apply_model_config(request.model or "smart")
    actor_id = _optional_actor(request.auth_token)
    event_kwargs = {
        "trigger": TriggerType.MESSAGE,
        "content": query,
        "session_id": request.session_id,
        "actor_id": actor_id,
        "channel": "web",
        "metadata": {"model": request.model or "smart"},
    }
    event_id = _event_id_from_request("chat", request.session_id, actor_id, idempotency_key)
    if event_id:
        event_kwargs["event_id"] = event_id
    run = investment_gateway.dispatch(AgentEvent(**event_kwargs))
    if run.route == "duplicate":
        raise HTTPException(status_code=409, detail="duplicate request is already recorded; retry without reusing the key after checking run status")
    payload = dict(run.payload)
    workflow_result = payload.pop("workflow_result", None)
    if workflow_result is not None:
        payload["review_id"] = _create_publication_review(workflow_result, request.auth_token)
    payload["run_id"] = run.run_id
    payload["route"] = run.route
    return payload

@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_stock(request: AnalyzeRequest, idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key")):
    try:
        stock_code = request.stock_code.strip()
        if not stock_code:
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        model = request.model or "smart"
        print(f"📨 收到分析请求：{stock_code} 模式：{model}")

        # 根据模型参数动态切换LLM
        _apply_model_config(model)
        actor_id = _optional_actor(request.auth_token)
        event_kwargs = {
            "trigger": TriggerType.HTTP,
            "content": f"分析 {stock_code}",
            "session_id": request.session_id,
            "actor_id": actor_id,
            "channel": "web",
            "metadata": {"operation": "analyze", "model": model},
        }
        event_id = _event_id_from_request("analyze", request.session_id, actor_id, idempotency_key)
        if event_id:
            event_kwargs["event_id"] = event_id
        run = investment_gateway.dispatch(AgentEvent(**event_kwargs))
        if run.route == "duplicate":
            raise HTTPException(status_code=409, detail="duplicate request is already recorded; retry without reusing the key after checking run status")
        payload = dict(run.payload)
        workflow_result = payload.pop("workflow_result", None)
        if workflow_result is None:
            raise HTTPException(status_code=400, detail=payload.get("content", "无法启动股票分析"))
        review_id = _create_publication_review(workflow_result, request.auth_token)
        return AnalyzeResponse(
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
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败：{str(e)}")


@router.get("/history/{stock_code}", response_model=HistoryResponse)
def get_history(stock_code: str):
    history = memory.get_history(stock_code)
    return HistoryResponse(stock_code=stock_code, history=history)


def _require_review_user(auth_token: str) -> str:
    identity = verify_token(auth_token)
    if not identity["valid"]:
        raise HTTPException(status_code=401, detail="valid X-Auth-Token is required")
    return identity["username"]


@router.get("/memory/preferences")
def get_user_preferences(x_auth_token: str = Header(...)):
    """Return only the authenticated user's explicit preference memory."""
    actor_id = _require_review_user(x_auth_token)
    return {"actor_id": actor_id, "preferences": runtime_memory.get_preferences(actor_id)}


@router.put("/memory/preferences")
def put_user_preferences(request: UserPreferencesRequest, x_auth_token: str = Header(...)):
    """Store explicit preferences; no model-inferred profile is accepted here."""
    actor_id = _require_review_user(x_auth_token)
    payload = request.model_dump(exclude_none=True) if hasattr(request, "model_dump") else request.dict(exclude_none=True)
    preferences = runtime_memory.set_preferences(
        actor_id,
        payload,
    )
    return {"actor_id": actor_id, "preferences": preferences}


@router.get("/memory/candidates")
def get_memory_candidates(status: str = "pending", x_auth_token: str = Header(...)):
    """Return the authenticated user's human-review queue, never indexed drafts."""
    actor_id = _require_review_user(x_auth_token)
    try:
        return {"candidates": list_candidates(requested_by=actor_id, status=status)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/memory/candidates")
def create_memory_candidate(request: MemoryCandidateRequest, x_auth_token: str = Header(...)):
    """Create a pending candidate. It does not enter Memory Index automatically."""
    actor_id = _require_review_user(x_auth_token)
    try:
        candidate = create_candidate(
            title=request.title, content=request.content, category=request.category,
            source_run_id=request.source_run_id, requested_by=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidate_id": candidate.candidate_id, "status": "pending"}


@router.post("/memory/candidates/bad-case")
def create_bad_case_candidate(request: BadCaseCandidateRequest, x_auth_token: str = Header(...)):
    """Online monitoring can submit a failure; it remains pending review."""
    actor_id = _require_review_user(x_auth_token)
    try:
        candidate = candidate_from_bad_case(
            request.model_dump() if hasattr(request, "model_dump") else request.dict(),
            requested_by=actor_id, source_run_id=request.source_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidate_id": candidate.candidate_id, "status": "pending"}


@router.post("/memory/candidates/backtest-deviation")
def create_backtest_deviation_candidate(
    request: BacktestDeviationCandidateRequest, x_auth_token: str = Header(...)
):
    """Measured backtest-vs-expectation deviations become reviewable candidates."""
    actor_id = _require_review_user(x_auth_token)
    try:
        candidate = candidate_from_backtest_deviation(
            expected=request.expected, actual=request.actual, requested_by=actor_id,
            source_run_id=request.source_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"candidate_id": candidate.candidate_id, "status": "pending"}


@router.get("/memory/candidates/{candidate_id}")
def get_memory_candidate(candidate_id: str, x_auth_token: str = Header(...)):
    actor_id = _require_review_user(x_auth_token)
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="memory candidate not found")
    if candidate.get("requested_by") != actor_id:
        raise HTTPException(status_code=403, detail="candidate belongs to another user")
    return candidate


@router.post("/memory/candidates/{candidate_id}/decision")
def decide_memory_candidate(
    candidate_id: str, request: MemoryCandidateDecisionRequest, x_auth_token: str = Header(...)
):
    """Approve -> write source Markdown; vectors still require an explicit sync."""
    reviewer = _require_review_user(x_auth_token)
    existing = get_candidate(candidate_id)
    if not existing:
        raise HTTPException(status_code=404, detail="memory candidate not found")
    if existing.get("requested_by") != reviewer:
        raise HTTPException(status_code=403, detail="candidate belongs to another user")
    resolved = review_candidate(
        candidate_id, approved=request.approved, reviewer=reviewer, review_note=request.review_note,
    )
    if not resolved:
        raise HTTPException(status_code=409, detail="candidate was already resolved")
    return {
        "candidate_id": candidate_id,
        "status": resolved["status"],
        "approved_path": resolved.get("approved_path"),
        "index_sync_required": bool(request.approved),
    }


@router.get("/reviews/{review_id}")
def get_publication_review(review_id: str, x_auth_token: str = Header(...)):
    """Fetch a pending draft and its evidence for the human reviewer."""
    review = get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="review not found")
    reviewer = _require_review_user(x_auth_token)
    if reviewer != review["payload"].get("requested_by"):
        raise HTTPException(status_code=403, detail="review belongs to another user")
    return review


@router.post("/reviews/{review_id}/decision")
def decide_publication_review(
    review_id: str, request: ReviewDecisionRequest, x_auth_token: str = Header(...)
):
    """HITL boundary: only an explicit approval publishes and saves a draft."""
    reviewer = _require_review_user(x_auth_token)
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


@router.get("/stocks/info/{stock_code}")
def get_stock_info(stock_code: str):
    try:
        from tools.akshare_tools import get_stock_price

        result = get_stock_price.invoke({"symbol": stock_code})
        return {"stock_code": stock_code, "info": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 新增：回测接口 ──────────────────────────


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest_api(request: BacktestRequest):
    """Execute through the shared service used by tool and workflow entrypoints."""
    from backtest.service import BacktestInputError, execute_backtest

    try:
        execution = execute_backtest(
            stock_code=request.stock_code,
            strategy=request.strategy,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_cash=request.initial_cash,
        )
    except BacktestInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"backtest failed: {exc}") from exc

    result = execution["result"]
    memory.save_backtest_result(
        stock_code=execution["stock_code"],
        strategy=execution["strategy"],
        result_summary=execution["report_text"][:500],
    )
    returns = result["returns_series"]
    return BacktestResponse(
        stock_code=execution["stock_code"],
        strategy=execution["strategy"],
        total_return=result["total_return"],
        sharpe=result["sharpe"],
        max_drawdown=result["max_drawdown"],
        trade_count=result["trade_count"],
        win_rate=result["win_rate"],
        report_text=execution["report_text"],
        report_path=result.get("report_path"),
        returns_data=[round(float(value), 6) for value in returns.values],
        dates_data=[str(value.date()) for value in returns.index],
        trade_records=result.get("trade_records", []),
    )


@router.get("/backtest/strategies")
def list_strategies():
    """列出所有可用的回测策略"""
    from backtest.strategies import STRATEGY_MAP

    return {
        "strategies": [
            {"name": "kdj_macd", "description": "KDJ金叉 + MACD确认（双重信号过滤）"},
            {"name": "rsi", "description": "RSI超卖买入 / 超买卖出"},
            {"name": "boll", "description": "布林带下轨买入 / 上轨卖出"},
        ]
    }


@router.get("/backtest/sectors")
def get_sectors():
    """获取所有板块列表"""
    from backtest.stock_universe import STOCK_UNIVERSE

    sectors = {}
    for sector, stocks in STOCK_UNIVERSE.items():
        sectors[sector] = [
            {"code": code, "name": name} for code, name in stocks.items()
        ]
    return {"sectors": sectors}


@router.get("/backtest/history/{stock_code}")
def get_backtest_history(stock_code: str):
    """获取某只股票的历史回测记录"""
    history = memory.get_backtest_history(stock_code)
    return {"stock_code": stock_code, "history": history}


class FilterRequest(BaseModel):
    sector: str
    min_score: float = 65.0
    top_n: int = 5


@router.post("/backtest/filter")
def filter_sector_stocks(request: FilterRequest):
    from backtest.stock_universe import STOCK_UNIVERSE
    from backtest.fundamental_filter import filter_stocks

    stocks = STOCK_UNIVERSE.get(request.sector, {})
    if not stocks:
        return {"results": []}
    results = filter_stocks(stocks, min_score=request.min_score, top_n=request.top_n)
    return {"results": results}


class ScanRequest(BaseModel):
    base_start: str = None
    top_n: int = 10
    strategy: str = "all"  # all/oversold/cross


@router.post("/scan/today")
def scan_today_signals(request: ScanRequest):
    try:
        from agent_runtime.compat.langgraph.scan_graph import run_daily_scan

        result = run_daily_scan(
            base_start=request.base_start, strategy=request.strategy
        )
        recommendations = result.get("final_recommendations", [])
        return {
            "date": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
            "total_candidates": len(result.get("candidates", [])),
            "recommendations": recommendations,
            "count": len(recommendations),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"扫描失败: {str(e)}")


# ── 多模态上传接口 ──────────────────────────────────────────────────────


@router.post("/upload/image")
async def upload_image(
    file: UploadFile = File(...),
    question: str = Form(default=""),
    session_id: str = Form(default="default_session"),
):
    """
    上传图片并用多模态LLM分析
    适合：财报截图、K线图、公告截图
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="只支持图片文件")

    max_size = 10 * 1024 * 1024  # 10MB
    file_bytes = await file.read()
    if len(file_bytes) > max_size:
        raise HTTPException(status_code=400, detail="图片大小不能超过10MB")

    print(f"[Upload] 收到图片：{file.filename}，大小：{len(file_bytes)/1024:.1f}KB")

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
):
    """
    上传文档（PDF/Word/CSV/TXT）并存入临时向量库
    分析时自动检索文档内容作为补充上下文
    """
    allowed_types = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "text/plain",
        "text/csv",
        "application/vnd.ms-excel",
    }

    max_size = 20 * 1024 * 1024  # 20MB
    file_bytes = await file.read()
    if len(file_bytes) > max_size:
        raise HTTPException(status_code=400, detail="文件大小不能超过20MB")

    print(
        f"[Upload] 收到文档：{file.filename}，大小：{len(file_bytes)/1024:.1f}KB，session：{session_id[:8]}"
    )

    result = process_document(file_bytes, file.filename, session_id)

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
def cleanup_session_route(session_id: str):
    """清理用户session的临时文档"""
    cleanup_session(session_id)
    return {"status": "ok", "message": f"session {session_id[:8]} 已清理"}


@router.delete("/upload/session/{session_id}/document/{document_id}")
def cleanup_document_route(session_id: str, document_id: str):
    deleted = cleanup_document(session_id, document_id)
    return {"status": "ok", "deleted_chunks": deleted}


# ── Alpha 因子打分接口 ──────────────────────────────────────────────────


class AlphaRequest(BaseModel):
    stocks: Optional[list] = None  # [(code, name), ...] 不传则用动态股票池
    min_score: float = 60
    top_n: int = 20
    sector: Optional[str] = None  # 指定板块筛选


class SingleAlphaRequest(BaseModel):
    stock_code: str
    stock_name: str = ""


@router.post("/alpha/score")
def alpha_score(request: AlphaRequest):
    """
    Alpha因子批量打分
    五因子：KDJ反转 + 成交量 + ROE + 市值 + 均线趋势
    评级：≥75重点关注 / 60-74值得关注 / <60不推荐
    """
    try:
        from backtest.alpha_factor import batch_score
        from backtest.stock_universe import get_dynamic_universe, STOCK_UNIVERSE

        # 确定股票池
        if request.stocks:
            stock_list = [(s[0], s[1]) for s in request.stocks]
        elif request.sector and request.sector in STOCK_UNIVERSE:
            stock_list = list(STOCK_UNIVERSE[request.sector].items())
        else:
            # 用动态股票池（缓存）
            stock_list = get_dynamic_universe(max_stocks=200, use_cache=True)

        print(f"[Alpha] 开始打分：{len(stock_list)} 只股票")

        scores = batch_score(
            stock_list=stock_list,
            min_score=request.min_score,
            top_n=request.top_n,
        )

        return {
            "total_scored": len(stock_list),
            "qualified": len(scores),
            "min_score": request.min_score,
            "results": [s.to_dict() for s in scores],
            "status": "success",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打分失败：{str(e)}")


@router.post("/alpha/single")
def alpha_single(request: SingleAlphaRequest):
    """
    单只股票Alpha因子打分
    """
    try:
        from backtest.alpha_factor import score_stock, format_score_report

        score = score_stock(
            request.stock_code, request.stock_name or request.stock_code
        )

        if score.error:
            raise HTTPException(status_code=400, detail=f"打分失败：{score.error}")

        return {
            **score.to_dict(),
            "report": format_score_report(score),
            "status": "success",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打分失败：{str(e)}")


# ── 用户认证接口 ────────────────────────────────────────────────────────

from api.auth import (
    register as _register,
    login as _login,
    verify_token as _verify,
    logout as _logout,
)


class AuthRequest(BaseModel):
    username: str
    password: str


class TokenRequest(BaseModel):
    token: str


@router.post("/auth/register")
def auth_register(request: AuthRequest):
    """用户注册"""
    result = _register(request.username, request.password)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/auth/login")
def auth_login(request: AuthRequest):
    """用户登录"""
    result = _login(request.username, request.password)
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["message"])
    return result


@router.post("/auth/verify")
def auth_verify(request: TokenRequest):
    """验证token"""
    return _verify(request.token)


@router.post("/auth/logout")
def auth_logout(request: TokenRequest):
    """登出"""
    return _logout(request.token)


# ── 对话记录持久化（PostgreSQL 版）──────────────────────────────────
import json as _json
from db import execute


@router.get("/conversations/{username}")
def get_conversations(username: str):
    """获取用户的对话记录"""
    from urllib.parse import unquote

    username = unquote(username)

    rows = execute(
        """
        SELECT id, title, messages
        FROM conversations_store
        WHERE username = %s
        ORDER BY updated_at DESC
        LIMIT 20
        """,
        (username,),
        fetch="all",
    )
    return {
        "conversations": [
            {"id": r[0], "title": r[1], "messages": _json.loads(r[2])}
            for r in (rows or [])
        ]
    }


class ConvSaveRequest(BaseModel):
    id: str
    username: str
    title: str
    messages: list


@router.post("/conversations/save")
def save_conversation(request: ConvSaveRequest):
    """保存对话记录"""
    from urllib.parse import unquote
    import datetime

    username = unquote(request.username)

    execute(
        """
        INSERT INTO conversations_store (id, username, title, messages, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            title      = EXCLUDED.title,
            messages   = EXCLUDED.messages,
            updated_at = EXCLUDED.updated_at
        """,
        (
            request.id,
            username,
            request.title,
            _json.dumps(request.messages, ensure_ascii=False),
            datetime.datetime.now().isoformat(),
        ),
    )
    return {"ok": True}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    """删除对话记录"""
    execute("DELETE FROM conversations_store WHERE id = %s", (conv_id,))
    return {"ok": True}
