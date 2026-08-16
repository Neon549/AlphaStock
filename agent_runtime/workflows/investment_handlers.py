"""Framework-neutral handlers extracted from the fixed investment graph."""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import re
from types import SimpleNamespace
from typing import Any

from agent_runtime.context.budget import ContextBlock, pack_context
from agent_runtime.reliability import classify_model_failure
from agent_runtime.workflows.governance import evaluate_output_gate, validate_analysis_scope


SKIPPED = "[SKIPPED] analyst branch was not requested"


def _get_trader_model():
    """Delay provider imports so the state-machine module remains testable offline."""
    from config.llm_config import deep_llm

    return deep_llm


def _get_long_term_memory():
    from agent_runtime.memory.long_term import LongTermMemory

    return LongTermMemory()


def _human_message(content: str):
    """Use LangChain's message object in production, with a minimal test fallback."""
    try:
        from langchain_core.messages import HumanMessage
    except ModuleNotFoundError:
        return SimpleNamespace(content=content)
    return HumanMessage(content=content)


def _failed(value: str | None) -> bool:
    return not value or value.strip().startswith(("[ANALYSIS_ABORT]", "[TOOL_ERROR]"))


def _safe_analyst_call(name: str, invoke) -> tuple[str, dict[str, Any] | None]:
    """Contain a model outage in one analyst branch instead of failing the graph."""
    try:
        return invoke(), None
    except Exception as exc:
        failure = classify_model_failure(exc)
        return (
            f"[ANALYSIS_ABORT] {name} unavailable; error_type={failure.error_type.value}",
            failure.to_dict(),
        )


def policy_guard_node(state: dict[str, Any]) -> dict[str, Any]:
    policy = validate_analysis_scope(state.get("stock_code", ""), state.get("analyst_focus") or "all", state.get("user_doc_context") or "")
    if not policy["allowed"]:
        reason = "; ".join(policy["violations"])
        return {"policy_decision": policy, "risk_assessment": "[POLICY_DENIED] " + reason, "final_decision": "[PUBLISH_BLOCKED] " + reason, "publish_status": "blocked", "publish_reasons": policy["violations"]}
    return {"policy_decision": policy}


def analysts_node(state: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.agents.fundamental_analyst import run_fundamental_analysis
    from agent_runtime.agents.sentiment_analyst import run_sentiment_analysis
    from agent_runtime.agents.technical_analyst import run_technical_analysis

    code, focus = state["stock_code"], state.get("analyst_focus") or "all"
    def fundamental():
        return _safe_analyst_call(
            "fundamental analyst",
            lambda: run_fundamental_analysis(code, state.get("user_doc_context") or ""),
        ) if focus in ("all", "fundamental") else (SKIPPED, None)

    def technical():
        return _safe_analyst_call(
            "technical analyst", lambda: run_technical_analysis(code)
        ) if focus in ("all", "technical") else (SKIPPED, None)

    def sentiment():
        return _safe_analyst_call(
            "sentiment analyst", lambda: run_sentiment_analysis(code)
        ) if focus in ("all", "sentiment") else (SKIPPED, None)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(contextvars.copy_context().run, fn) for fn in (fundamental, technical, sentiment)]
        (fundamental_report, fundamental_failure), (technical_report, technical_failure), (sentiment_report, sentiment_failure) = [future.result() for future in futures]
    return {
        "fundamental_report": fundamental_report,
        "technical_report": technical_report,
        "sentiment_report": sentiment_report,
        "model_failures": [
            failure for failure in (fundamental_failure, technical_failure, sentiment_failure) if failure
        ],
    }


def context_snapshot_node(state: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.context.snapshot import build_context_snapshot

    return {"context_snapshot": build_context_snapshot(state["stock_code"], {"technical": state.get("technical_report"), "fundamental": state.get("fundamental_report"), "sentiment": state.get("sentiment_report")}, document_citations=state.get("document_citations") or [])}


def validation_node(state: dict[str, Any]) -> dict[str, Any]:
    reports = [state.get(name) for name in ("fundamental_report", "technical_report", "sentiment_report")]
    failures = [report for report in reports if report != SKIPPED and _failed(report)]
    if len(failures) >= 3:
        return {
            "risk_assessment": "all requested analyst branches failed",
            "final_decision": "[PUBLISH_BLOCKED] insufficient verified evidence",
            "publish_status": "blocked",
            "publish_reasons": ["all requested analyst branches failed"],
            "human_review_required": bool(state.get("model_failures")),
        }
    if failures and state.get("replan_attempts", 0) < 1:
        return {"risk_assessment": "partial analyst failure", "replan_required": True}
    return {"risk_assessment": "validation passed" if not failures else "partial evidence only", "replan_required": False}


def replan_node(state: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.agents.fundamental_analyst import run_fundamental_analysis
    from agent_runtime.agents.sentiment_analyst import run_sentiment_analysis
    from agent_runtime.agents.technical_analyst import run_technical_analysis

    updates: dict[str, Any] = {"replan_attempts": state.get("replan_attempts", 0) + 1, "replan_required": False}
    failures = list(state.get("model_failures") or [])
    if _failed(state.get("fundamental_report")):
        updates["fundamental_report"], failure = _safe_analyst_call(
            "fundamental analyst", lambda: run_fundamental_analysis(state["stock_code"])
        )
        if failure:
            failures.append(failure)
    if _failed(state.get("technical_report")):
        updates["technical_report"], failure = _safe_analyst_call(
            "technical analyst", lambda: run_technical_analysis(state["stock_code"])
        )
        if failure:
            failures.append(failure)
    if _failed(state.get("sentiment_report")):
        updates["sentiment_report"], failure = _safe_analyst_call(
            "sentiment analyst", lambda: run_sentiment_analysis(state["stock_code"])
        )
        if failure:
            failures.append(failure)
    updates["model_failures"] = failures
    return updates


def abort_node(state: dict[str, Any]) -> dict[str, Any]:
    return {"bull_argument": "workflow aborted before research", "bear_argument": state.get("risk_assessment", "policy or evidence failure")}


def researcher_node(state: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.agents.research_harness import run_research_harness
    from agent_runtime.evidence.cards import build_evidence_cards

    result = run_research_harness(stock_code=state["stock_code"], snapshot=state.get("context_snapshot") or {}, session_id=state.get("session_id"), request_query=state.get("analysis_query") or state["stock_code"], runtime_context=state.get("agent_context") or "", actor_id=state.get("actor_id"), granted_permissions={"document:read", "market:read", "memory:read"})
    observations = result["observations"]
    return {
        "bull_argument": result["report"], "bear_argument": result["report"],
        "debate_rounds": state.get("debate_rounds", 0) + 1,
        "agent_trace": result["trace"], "research_evidence": observations,
        "evidence_cards": build_evidence_cards(observations),
        "model_failures": [
            *(state.get("model_failures") or []), *(result.get("model_failures") or []),
        ],
    }


def _calc_position_size(decision_text: str, confidence: str) -> str:
    """Map a buy decision and confidence to a deterministic position size."""
    if "强烈买入" in decision_text:
        return {"高": "30%", "中": "20%", "低": "10%"}.get(confidence, "15%")
    if "买入" in decision_text:
        return {"高": "20%", "中": "10%", "低": "5%"}.get(confidence, "10%")
    return "0%"


def _fix_position_consistency(text: str, confidence: str) -> str:
    """Correct only the position field; the draft itself remains model-generated."""
    is_buy = any(item in text for item in ("强烈买入", "买入"))
    is_watch = any(item in text for item in ("持有观望", "观望", "不买", "停止分析"))
    if is_buy and not is_watch:
        return re.sub(r"建议仓位：?[^\n]*", f"建议仓位：{_calc_position_size(text, confidence)}", text)
    if is_watch:
        return re.sub(r"建议仓位：?[^\n]*", "建议仓位：0%", text)
    return text


def _trader_confidence(risk_assessment: str) -> str:
    if "置信度：高" in risk_assessment:
        return "高"
    if "置信度：低" in risk_assessment:
        return "低"
    return "中"


def trader_node(state: dict[str, Any]) -> dict[str, Any]:
    """Produce a decision draft from bounded, traceable research context.

    This handler deliberately owns no graph state or routing. It can therefore
    be invoked unchanged by LangGraph or by the explicit Python state machine.
    """
    history = _get_long_term_memory().get_history(state["stock_code"])
    snapshot_text = json.dumps(state.get("context_snapshot") or {}, ensure_ascii=False, indent=2)
    risk_assessment = state.get("risk_assessment") or ""
    packed_context = pack_context(
        [
            ContextBlock("structured evidence snapshot", snapshot_text, 100),
            ContextBlock("current risk assessment", risk_assessment, 90),
            ContextBlock(
                "session continuation and user preferences (non-evidence)",
                json.dumps(state.get("memory_context") or {}, ensure_ascii=False),
                70,
            ),
            ContextBlock("recent approved decisions", history, 60),
        ]
    )
    print(
        f"[ContextBudget] mode={packed_context['mode']} "
        f"estimated_tokens={packed_context['estimated_tokens']} "
        f"omitted={packed_context['omitted_blocks']}"
    )

    prompt = f"""你是一位经验丰富的 A 股交易员，需要基于研究团队的分析做出最终交易决策。

【强制规则】
1. 只能依据已提供内容决策；用户偏好和会话信息不是市场证据。
2. 买入/强烈买入必须给出具体百分比仓位；持有观望/不买必须为 0%。
3. 价格必须是具体数字；不得补造市场数据或承诺收益。
4. 即使部分数据缺失，也要基于已有证据输出买入、持有观望或不买，而非杜撰事实。

## 已打包的可追溯上下文
{packed_context['text']}

## 研究结论
{risk_assessment or state.get('bull_argument', '无')}

请按以下格式输出：
### 交易决策
决策：[强烈买入 / 买入 / 持有观望 / 不买]
建议仓位：[具体百分比]
操作价位：[具体价格区间]
目标价：[具体目标价]
止损价：[具体止损价]
持有周期：[短线1-2周 / 中线1-3月]

### 决策依据
[3条核心理由]

### 风险提示
[2-3条主要风险]
"""
    try:
        raw_decision = _get_trader_model().invoke([_human_message(prompt)]).content
    except Exception as exc:
        failure = classify_model_failure(exc)
        return {
            "final_decision": "[PUBLISH_BLOCKED] 模型服务不可用，未生成投资结论。",
            "model_failures": [
                *(state.get("model_failures") or []), failure.to_dict(),
            ],
        }
    final_decision = _fix_position_consistency(raw_decision, _trader_confidence(risk_assessment))
    return {"final_decision": final_decision}


def output_gate_node(state: dict[str, Any]) -> dict[str, Any]:
    """Apply the deterministic publication policy after draft generation."""
    result = evaluate_output_gate(state)
    print(f"[OutputGate] status={result['publish_status']}")
    return result


def default_handlers() -> dict[str, Any]:
    """Return the complete fixed-workflow handler set for the Python runtime."""
    return {
        "policy_guard": policy_guard_node,
        "analysts": analysts_node,
        "context_snapshot": context_snapshot_node,
        "validation": validation_node,
        "replan": replan_node,
        "abort": abort_node,
        "researcher": researcher_node,
        "trader": trader_node,
        "output_gate": output_gate_node,
    }
