"""Framework-neutral runtime interface for the fixed investment workflow."""

from __future__ import annotations

from typing import Any, Protocol


class InvestmentWorkflowRuntime(Protocol):
    def run(self, stock_code: str, **kwargs: Any) -> dict[str, Any]: ...


class BacktestWorkflowRuntime(Protocol):
    def run(self, stock_code: str, **kwargs: Any) -> dict[str, Any]: ...


class LangGraphInvestmentRuntime:
    """Compatibility adapter; production callers use the Python runtime by default."""

    def run(self, stock_code: str, **kwargs: Any) -> dict[str, Any]:
        from agent_runtime.compat.langgraph.trading_graph import run_trading_analysis
        return run_trading_analysis(stock_code, **kwargs)


class PythonInvestmentRuntime:
    """Explicit state-machine runtime for the complete fixed investment flow.

    Supplying handlers keeps unit tests framework/provider-free. With no
    argument it loads the production handler registry and applies the same
    per-run model profile isolation as the LangGraph adapter.
    """

    def __init__(self, handlers=None):
        self.handlers = handlers

    def run(self, stock_code: str, **kwargs: Any) -> dict[str, Any]:
        from agent_runtime.workflows.python_state_machine import run_fixed_workflow

        state = {"stock_code": stock_code, **kwargs}
        # Gateway/API names this input ``doc_context``. The fixed-state
        # contract calls it ``user_doc_context`` so analysts and policy checks
        # share one explicit field regardless of entry channel.
        if "user_doc_context" not in state:
            state["user_doc_context"] = state.pop("doc_context", "")
        if self.handlers is not None:
            return run_fixed_workflow(state, self.handlers)

        from control_plane.model_profile import model_scope
        from agent_runtime.workflows.investment_handlers import default_handlers

        with model_scope(str(state.get("model_profile") or "smart")):
            return run_fixed_workflow(state, default_handlers())


class LangGraphBacktestRuntime:
    """Compatibility adapter for the old LangGraph backtest graph."""

    def run(self, stock_code: str, **kwargs: Any) -> dict[str, Any]:
        from agent_runtime.compat.langgraph.trading_graph import run_backtest_analysis

        return run_backtest_analysis(stock_code, **kwargs)


class PythonBacktestRuntime:
    """Default bounded backtest runtime, independent of LangGraph."""

    def __init__(self, handlers=None):
        self.handlers = handlers

    def run(self, stock_code: str, **kwargs: Any) -> dict[str, Any]:
        from agent_runtime.workflows.backtest_state_machine import run_backtest_workflow

        request = {
            "stock_code": stock_code,
            "strategy": kwargs.pop("strategy", "kdj_macd"),
            "start_date": kwargs.pop("start_date", "20220101"),
            "end_date": kwargs.pop("end_date", "20261231"),
            "initial_cash": kwargs.pop("initial_cash", 100000.0),
        }
        state = {"stock_code": stock_code, "backtest_request": request, **kwargs}
        if self.handlers is not None:
            return run_backtest_workflow(state, self.handlers)

        from control_plane.model_profile import model_scope
        from agent_runtime.workflows.backtest_handlers import default_backtest_handlers

        with model_scope(str(state.get("model_profile") or "smart")):
            return run_backtest_workflow(state, default_backtest_handlers())
