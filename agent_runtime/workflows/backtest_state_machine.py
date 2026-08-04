"""Framework-neutral control flow for a single quantitative backtest run."""

from __future__ import annotations

from typing import Any, Callable, Mapping


BacktestHandler = Callable[[dict[str, Any]], dict[str, Any]]


def run_backtest_workflow(
    state: dict[str, Any], handlers: Mapping[str, BacktestHandler]
) -> dict[str, Any]:
    """Run execution, interpretation and optimisation in a fixed safe order."""

    for name in ("backtest", "interpreter"):
        state.update(handlers[name](state) or {})

    # A failed data/backtest call must not trigger a grid search on unrelated
    # mock data merely to produce an apparently complete report.
    if "[TOOL_ERROR]" in (state.get("backtest_report") or ""):
        return state

    state.update(handlers["optimizer"](state) or {})
    return state
