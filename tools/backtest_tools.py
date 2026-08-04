"""LangChain tool adapters over the shared quantitative backtest service."""

from __future__ import annotations

from langchain_core.tools import tool

from backtest.service import BacktestInputError, execute_backtest


def _error(reason: str) -> str:
    return (
        "[TOOL_ERROR]\n"
        "tool=run_strategy_backtest\n"
        f"reason={reason}\n"
        "结论：回测失败，无法生成策略评估报告。"
    )


def _ok(body: str) -> str:
    return f"[TOOL_OK]\ntool=run_strategy_backtest\n{body}"


@tool
def run_strategy_backtest(
    stock_code: str,
    strategy: str = "kdj_macd",
    start_date: str = "20220101",
    end_date: str = "20261231",
    initial_cash: float = 100000.0,
) -> str:
    """Run a named A-share strategy and return a traceable text report."""

    try:
        execution = execute_backtest(
            stock_code=stock_code,
            strategy=strategy,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
        )
        return _ok(execution["report_text"] + f"\ndata_source={execution['data_source']}")
    except BacktestInputError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f"backtest execution failed: {type(exc).__name__}: {exc}")


@tool
def list_available_strategies() -> str:
    """List supported strategies without executing a backtest."""

    from backtest.strategies import STRATEGY_MAP

    return "\n".join(f"- {name}" for name in sorted(STRATEGY_MAP))


BACKTEST_TOOLS = [run_strategy_backtest, list_available_strategies]
