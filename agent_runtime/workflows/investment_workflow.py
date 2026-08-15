"""Bounded local investment-research workflow.

The analysis branch and the backtest branch are independent and may run in
parallel. Their outputs are synthesized into a read-only advisory result. The
deterministic output gate decides whether it can be returned; this workflow
never executes a trade.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any

def run_local_investment_workflow(
    stock_code: str,
    *,
    strategy: str = "kdj_macd",
    start_date: str = "20220101",
    end_date: str = "20261231",
    initial_cash: float = 100000.0,
    analyst_focus: str = "all",
    doc_context: str = "",
) -> dict[str, Any]:
    """Run analysis and backtest in parallel, then produce an advisory result.

    This is deliberately an orchestrated workflow, not an unbounded tool loop:
    both branches run once, and the final synthesis gets a bounded context.
    """
    from config.llm_config import deep_llm
    from agent_runtime.workflows.governance import evaluate_output_gate
    from agent_runtime.workflows.runtime import PythonBacktestRuntime, PythonInvestmentRuntime
    from langchain_core.messages import HumanMessage

    with ThreadPoolExecutor(max_workers=2) as executor:
        analysis_future = executor.submit(
            PythonInvestmentRuntime().run,
            stock_code,
            doc_context=doc_context,
            analyst_focus=analyst_focus,
        )
        backtest_future = executor.submit(
            PythonBacktestRuntime().run,
            stock_code,
            strategy=strategy,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
        )
        analysis = analysis_future.result()
        backtest = backtest_future.result()

    # Do not pass arbitrarily sliced analyst prose into the synthesis call.
    # The snapshot keeps status, numeric fields and evidence references, while
    # raw reports remain traceable in the branch results for later inspection.
    from agent_runtime.context.budget import ContextBlock, pack_context
    from agent_runtime.context.compaction import compact_tool_observations
    from agent_runtime.context.snapshot import build_context_snapshot

    analysis_snapshot = analysis.get("context_snapshot") or build_context_snapshot(
        stock_code,
        {
            "technical": analysis.get("technical_report"),
            "fundamental": analysis.get("fundamental_report"),
            "sentiment": analysis.get("sentiment_report"),
        },
        document_citations=analysis.get("document_citations") or [],
    )
    backtest_view, _ = compact_tool_observations(
        [{
            "tool": "backtest",
            "ok": "[TOOL_ERROR]" not in (backtest.get("backtest_report") or ""),
            "content": backtest.get("backtest_summary") or backtest.get("backtest_report") or "",
            "source_kind": "backtest_evidence",
            "tool_metadata": {"strategy": strategy, "start_date": start_date, "end_date": end_date},
        }],
        max_tokens=1_500,
        preview_chars=1_200,
    )
    synthesis_context = pack_context([
        ContextBlock("structured analyst evidence snapshot", json.dumps(analysis_snapshot, ensure_ascii=False), 100),
        ContextBlock("backtest evidence view", json.dumps(backtest_view, ensure_ascii=False), 90),
    ])

    prompt = f"""你是 A 股投研工作流的最终研究整合节点。
只能依据以下已完成的分析和历史回测结果形成研究草案；不能补造数据、承诺收益或把历史回测当作未来保证。
若基本面/技术面/情绪面与回测方向冲突，明确指出冲突和需要继续核验的证据。

股票代码：{stock_code}
回测策略：{strategy}；区间：{start_date}-{end_date}

## 已打包证据上下文
{synthesis_context['text']}

请输出：
1. 方向判断（偏多 / 偏空 / 中性 / 证据不足）；
2. 技术、基本面、情绪、回测各一条关键证据；
3. 一致或冲突说明；
4. 风险与下一步核验项。
这是只读投研建议，不是自动交易指令。"""
    draft = deep_llm.invoke([HumanMessage(content=prompt)]).content

    gate_state = {
        "fundamental_report": analysis.get("fundamental_report", ""),
        "technical_report": analysis.get("technical_report", ""),
        "sentiment_report": analysis.get("sentiment_report", ""),
        "final_decision": draft,
    }
    gate = evaluate_output_gate(gate_state)
    return {
        "workflow": "analysis_plus_backtest",
        "stock_code": stock_code,
        "strategy": strategy,
        "analysis": analysis,
        "backtest": backtest,
        "draft": gate.get("draft_decision", draft),
        "publish_status": gate["publish_status"],
        "publish_reasons": gate["publish_reasons"],
        "human_review_required": gate["human_review_required"],
    }
