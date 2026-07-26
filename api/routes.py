# api/routes.py
# ============ 改动说明 ============
# 新增: POST /api/v1/backtest 回测接口
# 新增: BacktestRequest / BacktestResponse 模型
# 新增: POST /api/v1/chat 自然语言对话接口（含意图识别）
# 原有接口不变
# ==================================

from fastapi import APIRouter, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
from graph.trading_graph import run_trading_analysis
from memory.long_term import LongTermMemory
from api.multimodal import (
    analyze_image,
    process_document,
    retrieve_from_document,
    cleanup_session,
)
from api.intent_parser import parse_intent  # ← 意图识别

router = APIRouter()
memory = LongTermMemory()


def _apply_model_config(model: str):
    """
    根据前端选择的模型，动态切换后端LLM配置
    fast:   DeepSeek-V3（快速便宜，适合选股筛选）
    smart:  DeepSeek-R1（推理强，适合深度分析）默认
    strong: DeepSeek-R1 + 更低temperature（严格，适合量化回测）
    """
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


class AnalyzeResponse(BaseModel):
    stock_code: str
    decision: str
    fundamental_report: str
    technical_report: str
    sentiment_report: str
    researcher_analysis: str
    status: str = "success"


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


# ── 原有接口 ────────────────────────────────


@router.get("/health")
def health_check():
    return {"status": "ok", "message": "Trading Agent System is running"}


# ── 新增：自然语言对话接口 ──────────────────


@router.post("/chat")
def chat(request: ChatRequest):
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

    # ── Step 1: 意图解析 ──────────────────────────
    parsed = parse_intent(query)
    intent = parsed["intent"]
    stock_code = parsed["stock_code"]
    stock_name = parsed["stock_name"]
    analyst_focus = parsed["analyst_focus"] or "all"
    reply_hint = parsed["reply_hint"]

    print(
        f"[Chat] query={query!r} intent={intent} stock={stock_code} focus={analyst_focus}"
    )

    # ── Step 2: 路由 ──────────────────────────────

    # 意图4：信息不足，追问用户
    if intent == 4:
        return {"role": "assistant", "content": reply_hint, "intent": intent}

    # 意图1：开放性讨论，quick_llm 直接回答，不启动 Agent
    if intent == 1:
        import config.llm_config as llm_cfg

        system_prompt = (
            "你是一个专业的A股投资顾问，精通行业分析、商业模式解读和财报分析。"
            "请用专业但易懂的语言回答用户的问题，可以发表自己的观点，"
            "但需注明这是分析视角而非投资建议。"
        )
        response = llm_cfg.quick_llm.invoke(f"{system_prompt}\n\n用户：{query}").content
        return {"role": "assistant", "content": response, "intent": intent}

    # 意图3：系统功能，引导用户使用对应模块
    if intent == 3:
        return {
            "role": "assistant",
            "content": "请使用左侧功能菜单选择【回测】、【扫描】或【筛选】功能，输入对应参数即可执行。",
            "intent": intent,
        }

    # 意图2：操作性分析，走完整 LangGraph 流程
    _apply_model_config(request.model or "smart")

    doc_context = ""
    if request.session_id:
        doc_context = retrieve_from_document(
            request.session_id, f"{stock_code} 财务 分析"
        )
        if doc_context:
            print(f"[Chat] 检索到用户上传文档：{len(doc_context)}字")

    result = run_trading_analysis(
        stock_code,
        doc_context=doc_context,
        analyst_focus=analyst_focus,  # ← 按需启动 Analyst
    )

    return {
        "role": "assistant",
        "intent": intent,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "analyst_focus": analyst_focus,
        "decision": result.get("final_decision", ""),
        "fundamental_report": result.get("fundamental_report", ""),
        "technical_report": result.get("technical_report", ""),
        "sentiment_report": result.get("sentiment_report", ""),
        "researcher_analysis": result.get("bull_argument", ""),
        "status": "success",
    }


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_stock(request: AnalyzeRequest):
    try:
        stock_code = request.stock_code.strip()
        if not stock_code:
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        model = request.model or "smart"
        print(f"📨 收到分析请求：{stock_code} 模式：{model}")

        # 根据模型参数动态切换LLM
        _apply_model_config(model)

        # 如果用户上传了文档，把文档内容注入到分析上下文
        doc_context = ""
        if request.session_id:
            doc_context = retrieve_from_document(
                request.session_id, f"{stock_code} 财务 分析"
            )
            if doc_context:
                print(f"[Analyze] 检索到用户上传文档内容：{len(doc_context)}字")

        result = run_trading_analysis(stock_code, doc_context=doc_context)

        return AnalyzeResponse(
            stock_code=stock_code,
            decision=result.get("final_decision", ""),
            fundamental_report=result.get("fundamental_report", ""),
            technical_report=result.get("technical_report", ""),
            sentiment_report=result.get("sentiment_report", ""),
            researcher_analysis=result.get("bull_argument", ""),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败：{str(e)}")


@router.get("/history/{stock_code}", response_model=HistoryResponse)
def get_history(stock_code: str):
    history = memory.get_history(stock_code)
    return HistoryResponse(stock_code=stock_code, history=history)


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
    """
    独立回测接口 —— 不走完整的 Multi-Agent 分析流程
    直接执行策略回测，返回绩效指标
    """
    try:
        import os
        from backtest.data_loader import get_stock_data_tushare, get_mock_data
        from backtest.engine import run_backtest, format_result

        stock_code = request.stock_code.strip()
        print(
            f"[Backtest] 请求: {stock_code} {request.start_date}-{request.end_date} 策略:{request.strategy}"
        )
        if not stock_code:
            raise HTTPException(status_code=400, detail="股票代码不能为空")

        # 获取数据
        token = os.getenv("TUSHARE_TOKEN", "")
        if token:
            df = get_stock_data_tushare(
                stock_code, request.start_date, request.end_date, token
            )
        else:
            df = get_mock_data(stock_code, days=500)

        if df.empty or len(df) < 60:
            raise HTTPException(status_code=400, detail=f"数据不足(仅{len(df)}根K线)")

        # 执行回测
        result = run_backtest(
            df=df,
            strategy_name=request.strategy,
            initial_cash=request.initial_cash,
        )

        report_text = format_result(result)

        # 存入记忆（复用现有 long_term memory）
        memory.save_backtest_result(
            stock_code=stock_code,
            strategy=request.strategy,
            result_summary=report_text[:500],
        )
        trade_records: Optional[list] = None
        returns = result["returns_series"]
        returns_dates = [str(d.date()) for d in returns.index]
        returns_values = [round(float(v), 6) for v in returns.values]
        return BacktestResponse(
            stock_code=stock_code,
            strategy=request.strategy,
            total_return=result["total_return"],
            sharpe=result["sharpe"],
            max_drawdown=result["max_drawdown"],
            trade_count=result["trade_count"],
            win_rate=result["win_rate"],
            report_text=report_text,
            returns_data=returns_values,
            dates_data=returns_dates,
            trade_records=result.get("trade_records", []),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"回测失败：{str(e)}")


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
        from graph.scan_graph import run_daily_scan

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
    session_id: str = Form(default=""),
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
    return {
        "filename": file.filename,
        "extracted_data": result["extracted_data"],
        "analysis": result["analysis"],
        "data_type": result["data_type"],
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
        "session_id": session_id,
        "status": "success",
        "message": f"文档已处理，共{result['chunk_count']}个片段，分析时将自动参考此文档",
    }


@router.delete("/upload/session/{session_id}")
def cleanup_session_route(session_id: str):
    """清理用户session的临时文档"""
    cleanup_session(session_id)
    return {"status": "ok", "message": f"session {session_id[:8]} 已清理"}


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
