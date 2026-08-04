"""Single execution service shared by API, tools and workflow runtimes."""

from __future__ import annotations

import os
import re
from typing import Any


class BacktestInputError(ValueError):
    """Raised for invalid requests or insufficient market history."""


def execute_backtest(
    *,
    stock_code: str,
    strategy: str = "kdj_macd",
    start_date: str = "20220101",
    end_date: str = "20261231",
    initial_cash: float = 100000.0,
) -> dict[str, Any]:
    """Load data once, run one strategy and preserve the raw result contract."""

    from backtest.data_loader import get_mock_data, get_stock_data_tushare
    from backtest.engine import format_result, run_backtest
    from backtest.strategies import STRATEGY_MAP

    code = (stock_code or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise BacktestInputError("stock_code must be a six-digit A-share code")
    if strategy not in STRATEGY_MAP:
        raise BacktestInputError(f"unsupported strategy: {strategy}")
    if initial_cash <= 0:
        raise BacktestInputError("initial_cash must be positive")

    token = os.getenv("TUSHARE_TOKEN", "")
    source = "tushare" if token else "mock"
    data = (
        get_stock_data_tushare(code, start_date, end_date, token)
        if token
        else get_mock_data(code, days=500)
    )
    if data is None or data.empty or len(data) < 60:
        raise BacktestInputError(f"insufficient history: {0 if data is None else len(data)} rows")

    # Runtime/API callers need deterministic metrics first. QuantStats HTML is
    # an optional presentation artifact and must not load into the critical
    # execution path for every agent run.
    result = run_backtest(
        data,
        strategy_name=strategy,
        initial_cash=initial_cash,
        generate_html_report=False,
    )
    return {
        "stock_code": code,
        "strategy": strategy,
        "data_source": source,
        "result": result,
        "report_text": format_result(result),
    }
