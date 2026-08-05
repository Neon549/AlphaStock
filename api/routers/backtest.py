"""Bounded quantitative backtest, screening and daily scan endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agent_runtime.memory.long_term import LongTermMemory
from api.security import require_actor
from control_plane.security import SecurityOperation, authorize_operation


router = APIRouter(tags=["backtest"])
memory = LongTermMemory()


class BacktestRequest(BaseModel):
    stock_code: str
    strategy: str = "kdj_macd"
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


class FilterRequest(BaseModel):
    sector: str
    min_score: float = 65.0
    top_n: int = 5


class ScanRequest(BaseModel):
    base_start: str | None = None
    top_n: int = 10
    strategy: str = "all"


def _authorize(tool: str, target: str, actor_id: str) -> None:
    try:
        authorize_operation(
            SecurityOperation(tool=tool, target=target, actor_id=actor_id), mode="auto"
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="operation is not permitted") from exc


@router.post("/backtest", response_model=BacktestResponse)
def run_backtest_api(
    request: BacktestRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    from backtest.service import BacktestInputError, execute_backtest

    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    _authorize("backtest", request.strategy, actor_id)
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
    except Exception:
        raise HTTPException(status_code=500, detail="backtest failed")

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
    return {
        "strategies": [
            {"name": "kdj_macd", "description": "KDJ金叉 + MACD确认（双重信号过滤）"},
            {"name": "rsi", "description": "RSI超卖买入 / 超买卖出"},
            {"name": "boll", "description": "布林带下轨买入 / 上轨卖出"},
        ]
    }


@router.get("/backtest/sectors")
def get_sectors():
    from backtest.stock_universe import STOCK_UNIVERSE

    return {
        "sectors": {
            sector: [{"code": code, "name": name} for code, name in stocks.items()]
            for sector, stocks in STOCK_UNIVERSE.items()
        }
    }


@router.get("/backtest/history/{stock_code}")
def get_backtest_history(stock_code: str):
    return {"stock_code": stock_code, "history": memory.get_backtest_history(stock_code)}


@router.post("/backtest/filter")
def filter_sector_stocks(
    request: FilterRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    _authorize("backtest", "filter", actor_id)
    from backtest.fundamental_filter import filter_stocks
    from backtest.stock_universe import STOCK_UNIVERSE

    stocks = STOCK_UNIVERSE.get(request.sector, {})
    return {"results": filter_stocks(stocks, min_score=request.min_score, top_n=request.top_n) if stocks else []}


@router.post("/scan/today")
def scan_today_signals(
    request: ScanRequest,
    x_auth_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    actor_id = require_actor(x_auth_token=x_auth_token, authorization=authorization)
    _authorize("agent", "scan", actor_id)
    try:
        from agent_runtime.workflows.scan_state_machine import run_daily_scan

        result = run_daily_scan(
            base_start=request.base_start,
            strategy=request.strategy,
            top_n=request.top_n,
        )
        recommendations = result.get("final_recommendations", [])
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "total_candidates": len(result.get("candidates", [])),
            "recommendations": recommendations,
            "count": len(recommendations),
            "analysis_errors": result.get("analysis_errors", []),
            "runtime": result.get("runtime", "python_state_machine"),
        }
    except Exception:
        raise HTTPException(status_code=500, detail="scan failed")
