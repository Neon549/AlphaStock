"""Thin LangGraph compatibility adapter over the shared Python handlers.

The default production path is ``PythonInvestmentRuntime`` / 
``PythonBacktestRuntime``. This module exists only for contract comparison,
checkpoint experiments and an explicit rollback switch.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from agent_runtime.compat.langgraph.state import TradingState
from agent_runtime.workflows.backtest_handlers import (
    backtest_interpreter_node,
    backtest_node,
    backtest_optimizer_node,
)
from agent_runtime.workflows.investment_handlers import (
    abort_node,
    analysts_node,
    context_snapshot_node,
    output_gate_node,
    policy_guard_node,
    replan_node,
    researcher_node,
    trader_node,
    validation_node,
)
from config.runtime_paths import CHECKPOINT_DB_PATH


def should_continue_after_policy(state: TradingState) -> str:
    return "abort" if state.get("publish_status") == "blocked" else "analysts"


def should_continue_after_validation(state: TradingState) -> str:
    if state.get("final_decision"):
        return "abort"
    return "replan" if state.get("replan_required") else "researcher"


def should_continue_after_backtest(state: TradingState) -> str:
    """Match PythonBacktestRuntime: do not optimise after a failed run."""

    return "end" if "[TOOL_ERROR]" in (state.get("backtest_report") or "") else "optimizer"


def build_trading_graph():
    """Build a graph using the same node handlers as the Python runtime."""

    graph = StateGraph(TradingState)
    graph.add_node("policy_guard", policy_guard_node)
    graph.add_node("analysts", analysts_node)
    graph.add_node("context_snapshot", context_snapshot_node)
    graph.add_node("validation", validation_node)
    graph.add_node("replan", replan_node)
    graph.add_node("abort", abort_node)
    graph.add_node("researcher", researcher_node)
    graph.add_node("trader", trader_node)
    graph.add_node("output_gate", output_gate_node)

    graph.set_entry_point("policy_guard")
    graph.add_conditional_edges(
        "policy_guard",
        should_continue_after_policy,
        {"abort": "abort", "analysts": "analysts"},
    )
    graph.add_edge("analysts", "context_snapshot")
    graph.add_edge("context_snapshot", "validation")
    graph.add_conditional_edges(
        "validation",
        should_continue_after_validation,
        {"abort": "abort", "replan": "replan", "researcher": "researcher"},
    )
    graph.add_edge("replan", "context_snapshot")
    graph.add_edge("researcher", "trader")
    graph.add_edge("trader", "output_gate")
    graph.add_edge("output_gate", END)
    graph.add_edge("abort", END)

    CHECKPOINT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False)
    return graph.compile(checkpointer=SqliteSaver(connection))


def build_backtest_graph():
    """Compatibility graph; nodes are shared with PythonBacktestRuntime."""

    graph = StateGraph(TradingState)
    graph.add_node("backtest", backtest_node)
    graph.add_node("backtest_interpreter", backtest_interpreter_node)
    graph.add_node("backtest_optimizer", backtest_optimizer_node)
    graph.set_entry_point("backtest")
    graph.add_edge("backtest", "backtest_interpreter")
    graph.add_conditional_edges(
        "backtest_interpreter",
        should_continue_after_backtest,
        {"end": END, "optimizer": "backtest_optimizer"},
    )
    graph.add_edge("backtest_optimizer", END)
    return graph.compile()


trading_graph = build_trading_graph()
backtest_graph = build_backtest_graph()


def run_trading_analysis(
    stock_code: str,
    doc_context: str = "",
    analyst_focus: str = "all",
    document_citations: list[dict] | None = None,
    session_id: str | None = None,
    analysis_query: str = "",
    memory_context: dict | None = None,
    agent_context: str = "",
    model_profile: str = "smart",
) -> dict[str, Any]:
    """Execute the legacy graph with the canonical fixed-workflow state."""

    state = {
        "stock_code": stock_code,
        "analyst_focus": analyst_focus,
        "fundamental_report": None,
        "technical_report": None,
        "sentiment_report": None,
        "bull_argument": None,
        "bear_argument": None,
        "debate_rounds": 0,
        "final_decision": None,
        "risk_assessment": None,
        "policy_decision": None,
        "publish_status": None,
        "publish_reasons": None,
        "human_review_required": None,
        "draft_decision": None,
        "replan_attempts": 0,
        "replan_required": False,
        "messages": [],
        "user_doc_context": doc_context,
        "document_citations": document_citations or [],
        "context_snapshot": None,
        "session_id": session_id,
        "analysis_query": analysis_query,
        "memory_context": memory_context or {},
        "agent_context": agent_context,
        "model_profile": model_profile,
        "agent_trace": [],
        "research_evidence": [],
        "evidence_cards": [],
    }
    from control_plane.model_profile import model_scope

    with model_scope(model_profile):
        return trading_graph.invoke(state, config={"configurable": {"thread_id": f"analysis_{stock_code}"}})


def run_backtest_analysis(
    stock_code: str,
    strategy: str = "kdj_macd",
    start_date: str = "20220101",
    end_date: str = "20261231",
    initial_cash: float = 100000.0,
    model_profile: str = "smart",
) -> dict[str, Any]:
    """Execute the compatibility backtest graph with the shared request contract."""

    state = {
        "stock_code": stock_code,
        "backtest_request": {
            "stock_code": stock_code,
            "strategy": strategy,
            "start_date": start_date,
            "end_date": end_date,
            "initial_cash": initial_cash,
        },
        "backtest_report": None,
        "backtest_summary": None,
        "backtest_optimizer_ran": None,
        "backtest_optimizer_skipped": None,
        "backtest_optimizer_error": None,
        "messages": [],
    }
    from control_plane.model_profile import model_scope

    with model_scope(model_profile):
        return backtest_graph.invoke(state)
