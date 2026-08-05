"""Governed Claude-Code-style loop for the main investment research request.

The model may choose *which* read-only research skills to run and in what
order.  The harness remains responsible for capability checks, bounded turns,
deduplication, evidence persistence, deterministic validation and publication
governance.  It deliberately cannot publish a recommendation or place trades.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import re
import time
from typing import Any, Callable

from agent_runtime.context.compaction import compact_tool_observations, persist_tool_result
from agent_runtime.context.snapshot import build_context_snapshot
from agent_runtime.evidence.cards import build_evidence_cards
from agent_runtime.workflows.governance import evaluate_output_gate
from agent_runtime.workflows.investment_handlers import (
    SKIPPED,
    abort_node,
    output_gate_node,
    replan_node,
    trader_node,
    validation_node,
)


MAX_AGENT_STEPS = 4
MAX_OBSERVATION_CHARS = 1_800
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)
_ALLOWED_FOCUSES = {"technical", "fundamental", "sentiment"}

_SKILL_CATALOG = {
    "analysis": {
        "permission": "market:read",
        "description": "Run one or more specialist analyses in parallel. Arguments: {focuses:[technical,fundamental,sentiment]}",
    },
    "document-rag": {
        "permission": "document:read",
        "description": "Search the current session's uploaded documents. Arguments: {query}",
    },
    "backtest": {
        "permission": "backtest:run",
        "description": "Run one bounded historical strategy backtest. Arguments: {strategy,start_date,end_date}",
    },
    "memory-search": {
        "permission": "memory:read",
        "description": "Search approved operational memory. It is guidance, never current market evidence. Arguments: {query}",
    },
}


def _parse_action(raw: str) -> dict[str, Any] | None:
    match = _JSON_OBJECT.search(raw or "")
    if not match:
        return None
    try:
        action = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if action.get("action") not in {"skill", "final"}:
        return None
    return action


def _trim(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= MAX_OBSERVATION_CHARS else text[:MAX_OBSERVATION_CHARS] + "\n[result truncated]"


def _model_content(llm: Any, prompt: str) -> str:
    return getattr(llm.invoke(prompt), "content", "")


def _normalise_focuses(value: Any, requested_focus: str) -> list[str]:
    if not isinstance(value, list):
        value = []
    focuses = [str(item) for item in value if str(item) in _ALLOWED_FOCUSES]
    if focuses:
        return list(dict.fromkeys(focuses))
    if requested_focus in _ALLOWED_FOCUSES:
        return [requested_focus]
    return ["technical", "fundamental", "sentiment"]


def _run_analysis_skill(state: dict[str, Any], focuses: list[str]) -> dict[str, Any]:
    from agent_runtime.agents.fundamental_analyst import run_fundamental_analysis
    from agent_runtime.agents.sentiment_analyst import run_sentiment_analysis
    from agent_runtime.agents.technical_analyst import run_technical_analysis

    stock_code = state["stock_code"]
    document_evidence = state.get("user_doc_context") or ""
    work: dict[str, Callable[[], str]] = {
        "technical": lambda: run_technical_analysis(stock_code),
        "fundamental": lambda: run_fundamental_analysis(stock_code, document_evidence),
        "sentiment": lambda: run_sentiment_analysis(stock_code),
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(focuses)) as executor:
        futures = {
            focus: executor.submit(contextvars.copy_context().run, work[focus])
            for focus in focuses
        }
        results = {focus: future.result() for focus, future in futures.items()}

    updates: dict[str, Any] = {}
    for focus in _ALLOWED_FOCUSES:
        report_key = f"{focus}_report"
        updates[report_key] = results.get(focus, state.get(report_key, SKIPPED))
    return {
        "ok": any(str(result).startswith("[ANALYSIS_OK]") for result in results.values()),
        "content": json.dumps(results, ensure_ascii=False),
        "updates": updates,
        "source_kind": "analyst_report",
    }


def _run_document_skill(state: dict[str, Any], query: str, granted: set[str]) -> dict[str, Any]:
    session_id = state.get("session_id")
    if not session_id:
        return {"ok": False, "content": "[TOOL_ERROR] no session document is available"}
    from agent_runtime.skills.registry import skill_registry

    result = skill_registry.execute(
        "document-rag",
        granted_permissions=granted,
        session_id=session_id,
        query=query,
    )
    context = result.get("context", "")
    citations = result.get("citations", [])
    return {
        "ok": bool(context),
        "content": context or "[no matching document evidence]",
        "citations": citations,
        "updates": {
            "user_doc_context": context,
            "document_citations": citations,
        },
        "source_kind": "document_evidence",
    }


def _run_backtest_skill(state: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.workflows.runtime import PythonBacktestRuntime

    result = PythonBacktestRuntime().run(
        state["stock_code"],
        strategy=str(arguments.get("strategy") or "kdj_macd"),
        start_date=str(arguments.get("start_date") or "20220101"),
        end_date=str(arguments.get("end_date") or "20261231"),
    )
    report = result.get("backtest_summary") or result.get("backtest_report") or ""
    return {
        "ok": "[TOOL_ERROR]" not in report,
        "content": report,
        "updates": {"backtest_result": result},
        "source_kind": "backtest_evidence",
    }


def _run_memory_skill(query: str) -> dict[str, Any]:
    from agent_runtime.memory.index import search_memory

    matches = search_memory(query)
    citations = [
        {
            "evidence_id": item["evidence_id"],
            "source_path": item["source_path"],
            "chunk_index": item["chunk_index"],
        }
        for item in matches
    ]
    content = "\n\n".join(f"[{item['evidence_id']}]\n{item['content']}" for item in matches)
    return {
        "ok": True,
        "content": content or "[no approved memory matched]",
        "citations": citations,
        "source_kind": "operational_memory",
    }


def _execute_skill(
    skill_name: str,
    *,
    state: dict[str, Any],
    arguments: dict[str, Any],
    granted: set[str],
) -> dict[str, Any]:
    if skill_name == "analysis":
        return _run_analysis_skill(
            state, _normalise_focuses(arguments.get("focuses"), state.get("analyst_focus") or "all")
        )
    if skill_name == "document-rag":
        return _run_document_skill(state, str(arguments.get("query") or state.get("analysis_query") or ""), granted)
    if skill_name == "backtest":
        return _run_backtest_skill(state, arguments)
    if skill_name == "memory-search":
        return _run_memory_skill(str(arguments.get("query") or state.get("analysis_query") or ""))
    raise ValueError(f"unsupported skill: {skill_name}")


def _research_report(
    *,
    stock_code: str,
    snapshot: dict[str, Any],
    observations: list[dict[str, Any]],
    final_llm: Any,
) -> str:
    compacted, _ = compact_tool_observations(observations)
    prompt = f"""You are the research synthesis step of a governed A-share agent.
Use only the structured snapshot and tool observations. Do not invent data or
promise returns. Explain conflicts, stale/missing evidence and tool failures.
Operational memory is process guidance only and must never become a current
company, price, financial or news claim. Return concise bull arguments, bear
arguments, an overall stance and next verification steps. This is research, not
a trade instruction.

Stock code: {stock_code}
Structured snapshot: {json.dumps(snapshot, ensure_ascii=False)}
Tool observations: {json.dumps(compacted, ensure_ascii=False)}"""
    return _model_content(final_llm, prompt)


def run_investment_agent_loop(
    state: dict[str, Any],
    *,
    planner_llm: Any | None = None,
    final_llm: Any | None = None,
    skill_executor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a bounded, auditable Skill loop and then apply deterministic governance."""
    if planner_llm is None or final_llm is None:
        from config.llm_config import deep_llm, planner_llm as default_planner_llm

        planner_llm = planner_llm or default_planner_llm
        final_llm = final_llm or deep_llm

    from agent_runtime.workflows.investment_handlers import policy_guard_node

    state = dict(state)
    state.update(policy_guard_node(state))
    if state.get("publish_status") == "blocked":
        state.update(abort_node(state))
        return state

    granted = {"document:read", "market:read", "backtest:run", "memory:read"}
    available = [
        {"name": name, "description": spec["description"]}
        for name, spec in _SKILL_CATALOG.items()
        if spec["permission"] in granted and (name != "document-rag" or state.get("session_id"))
    ]
    observations: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    executed: set[str] = set()
    executor = skill_executor or _execute_skill

    for step in range(MAX_AGENT_STEPS):
        compacted, compacted_changed = compact_tool_observations(observations)
        if compacted_changed:
            trace.append({"step": step + 1, "event": "microcompact"})
        prompt = f"""You are the planner of a governed A-share research agent. Return JSON only.
You may choose a read-only Skill, or finish when evidence is sufficient.
Allowed Skills: {json.dumps(available, ensure_ascii=False)}
Return exactly one of:
{{"action":"skill","skill":"one allowed name","arguments":{{}},"reason":"..."}}
{{"action":"final","reason":"..."}}
The stock code is server-bound to {state['stock_code']}; never request another code.
Only choose backtest when the user requested it or it materially resolves a stated uncertainty.
Current request: {state.get('analysis_query') or state['stock_code']}
Requested analysis focus: {state.get('analyst_focus') or 'all'}
Runtime context (rules and session data; not market evidence): {state.get('agent_context') or ''}
Previous observations: {json.dumps(compacted, ensure_ascii=False)}"""
        action = _parse_action(_model_content(planner_llm, prompt))
        if not action:
            trace.append({"step": step + 1, "event": "invalid_planner_output"})
            break
        if action["action"] == "final":
            trace.append({"step": step + 1, "event": "final", "reason": action.get("reason", "")})
            break

        skill_name = str(action.get("skill") or "")
        spec = _SKILL_CATALOG.get(skill_name)
        if not spec or skill_name not in {item["name"] for item in available}:
            trace.append({"step": step + 1, "event": "skill_denied", "skill": skill_name})
            break
        key = json.dumps({"skill": skill_name, "arguments": action.get("arguments") or {}}, sort_keys=True)
        if key in executed:
            trace.append({"step": step + 1, "event": "duplicate_skill_skipped", "skill": skill_name})
            continue
        executed.add(key)

        try:
            # Capability checks stay in the harness rather than in the model:
            # the planner can select only this static, read-only allowlist.
            # Publishing and trading are intentionally absent from the catalog.
            if spec["permission"] not in granted:
                raise PermissionError(f"missing granted permission: {spec['permission']}")
            started = time.monotonic()
            result = executor(
                skill_name,
                state=state,
                arguments=dict(action.get("arguments") or {}),
                granted=granted,
            )
            state.update(result.get("updates") or {})
            result_ref = persist_tool_result(
                tool=skill_name,
                content=str(result.get("content") or ""),
                source_kind=result.get("source_kind", "evidence"),
                citations=result.get("citations", []),
            )
            observation = {
                "tool": skill_name,
                "ok": bool(result.get("ok")),
                "content": _trim(result.get("content")),
                "citations": result.get("citations", []),
                "source_kind": result.get("source_kind", "evidence"),
                "result_ref": result_ref,
                "freshness": result.get("freshness", {}),
                "tool_metadata": result.get("tool_metadata", {}),
            }
            observations.append(observation)
            trace.append({
                "step": step + 1,
                "event": "skill_result",
                "skill": skill_name,
                "ok": observation["ok"],
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "result_ref": result_ref,
            })
        except Exception as exc:
            observations.append({"tool": skill_name, "ok": False, "content": f"[TOOL_ERROR] {exc}"})
            trace.append({"step": step + 1, "event": "skill_error", "skill": skill_name, "error": str(exc)[:300]})
    else:
        trace.append({"step": MAX_AGENT_STEPS, "event": "budget_exhausted"})

    # A malformed or overly conservative planner must not make the standard
    # analyse endpoint return an empty report. This is a safe, bounded fallback.
    if not any(state.get(f"{focus}_report") for focus in _ALLOWED_FOCUSES):
        fallback = _run_analysis_skill(
            state, _normalise_focuses([], state.get("analyst_focus") or "all")
        )
        state.update(fallback["updates"])
        observations.append({
            "tool": "analysis",
            "ok": fallback["ok"],
            "content": _trim(fallback["content"]),
            "citations": [],
            "source_kind": "analyst_report",
            "result_ref": "runtime:fallback-analysis",
        })
        trace.append({"event": "fallback_analysis", "reason": "planner did not run analysis"})

    state["context_snapshot"] = build_context_snapshot(
        state["stock_code"],
        {
            "technical": state.get("technical_report"),
            "fundamental": state.get("fundamental_report"),
            "sentiment": state.get("sentiment_report"),
        },
        document_citations=state.get("document_citations") or [],
    )
    state.update(validation_node(state))
    if state.get("final_decision"):
        state.update(abort_node(state))
        return state
    if state.get("replan_required"):
        state.update(replan_node(state))
        state["context_snapshot"] = build_context_snapshot(
            state["stock_code"],
            {
                "technical": state.get("technical_report"),
                "fundamental": state.get("fundamental_report"),
                "sentiment": state.get("sentiment_report"),
            },
            document_citations=state.get("document_citations") or [],
        )
        state.update(validation_node(state))
        if state.get("final_decision"):
            state.update(abort_node(state))
            return state

    report = _research_report(
        stock_code=state["stock_code"],
        snapshot=state["context_snapshot"],
        observations=observations,
        final_llm=final_llm,
    )
    state.update({
        "bull_argument": report,
        "bear_argument": report,
        "research_evidence": observations,
        "evidence_cards": build_evidence_cards(observations),
        "agent_trace": trace,
    })
    state.update(trader_node(state))
    state.update(output_gate_node(state))
    # Keep this direct evaluation as a final defensive invariant if callers
    # replace output_gate_node with an integration adapter.
    state.update(evaluate_output_gate(state))
    return state
