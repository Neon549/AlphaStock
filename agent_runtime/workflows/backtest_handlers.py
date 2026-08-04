"""Shared handlers for the pure-Python and LangGraph backtest runtimes."""

from __future__ import annotations

import os
from typing import Any


def _memory():
    from agent_runtime.memory.long_term import LongTermMemory

    return LongTermMemory()


def _model():
    from config.llm_config import deep_llm

    return deep_llm


def _human_message(content: str):
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=content)


def backtest_node(state: dict[str, Any]) -> dict[str, Any]:
    """Run the requested strategy and keep the raw report as evidence."""

    from tools.backtest_tools import run_strategy_backtest

    request = state.get("backtest_request") or {}
    stock_code = request.get("stock_code", state.get("stock_code", ""))
    if not stock_code:
        return {
            "backtest_report": "[TOOL_ERROR] tool=run_strategy_backtest reason=missing stock code",
            "backtest_summary": "Backtest was not run because the stock code is missing.",
            "backtest_optimizer_skipped": True,
        }

    report = run_strategy_backtest.invoke(
        {
            "stock_code": stock_code,
            "strategy": request.get("strategy", "kdj_macd"),
            "start_date": request.get("start_date", "20220101"),
            "end_date": request.get("end_date", "20261231"),
            "initial_cash": request.get("initial_cash", 100000.0),
        }
    )
    return {"backtest_report": report}


def backtest_interpreter_node(state: dict[str, Any]) -> dict[str, Any]:
    """Interpret raw metrics with strategy knowledge; never treat it as advice."""

    report = state.get("backtest_report", "")
    if "[TOOL_ERROR]" in report:
        return {
            "backtest_summary": "Backtest data is unavailable; no interpretation was generated.",
            "backtest_optimizer_skipped": True,
        }

    from backtest.strategy_knowledge import retrieve_backtest_knowledge

    request = state.get("backtest_request") or {}
    strategy = request.get("strategy", "kdj_macd")
    knowledge = retrieve_backtest_knowledge(f"{strategy} strategy Sharpe drawdown win rate")
    prompt = f"""You are an A-share quantitative research analyst. Interpret only the
provided historical backtest report and strategy knowledge. Do not promise future
returns or turn the result into a trading instruction.

## Backtest report
{report}

## Strategy knowledge
{knowledge}

Return concise Chinese sections: return quality, risk/drawdown, statistical
limitations, one or two validation ideas, and a conclusion that historical
backtesting is not investment advice."""
    summary = _model().invoke([_human_message(prompt)]).content
    _memory().save_backtest_result(
        stock_code=request.get("stock_code", state.get("stock_code", "")),
        strategy=strategy,
        result_summary=report[:500],
    )
    return {"backtest_summary": summary}


def backtest_optimizer_node(state: dict[str, Any]) -> dict[str, Any]:
    """Attach a bounded grid-search report without changing the original run."""

    request = state.get("backtest_request") or {}
    stock_code = request.get("stock_code", state.get("stock_code", ""))
    strategy = request.get("strategy", "kdj_macd")
    try:
        from backtest.data_loader import get_mock_data, get_stock_data_tushare
        from backtest.optimizer import format_optimization_result, grid_search

        token = os.getenv("TUSHARE_TOKEN", "")
        data = (
            get_stock_data_tushare(
                stock_code,
                request.get("start_date", "20220101"),
                request.get("end_date", "20261231"),
                token,
            )
            if token
            else get_mock_data(stock_code, days=500)
        )
        report = format_optimization_result(grid_search(data, strategy, top_n=3), strategy)
        _memory().save_backtest_result(
            stock_code=stock_code,
            strategy=f"{strategy}_optimized",
            result_summary=report[:500],
        )
        return {
            "backtest_summary": (state.get("backtest_summary") or "") + "\n\n---\n" + report,
            "backtest_optimizer_ran": True,
        }
    except Exception as exc:
        return {"backtest_optimizer_ran": False, "backtest_optimizer_error": str(exc)}


def default_backtest_handlers() -> dict[str, Any]:
    return {
        "backtest": backtest_node,
        "interpreter": backtest_interpreter_node,
        "optimizer": backtest_optimizer_node,
    }
