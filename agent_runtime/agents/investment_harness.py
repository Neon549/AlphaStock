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
from agent_runtime.reliability import (
    DEFAULT_TOOL_CACHE,
    RetryBudget,
    classify_model_failure,
    classify_tool_failure,
    invoke_with_failure_policy,
)
from agent_runtime.agents.subagents import (
    SubagentRegistry,
    SubagentResult,
    SubagentTask,
    subagent_registry,
)
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
    "market-price": {
        "permission": "market:read",
        "description": "Retrieve a timestamped current market-price evidence record. No arguments needed.",
    },
    "financial-indicators": {
        "permission": "market:read",
        "description": "Retrieve timestamped financial indicators with reporting-period freshness. No arguments needed.",
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
    if action.get("action") == "subagent":
        # Accept the singular form for callers that do not need parallel fan-out,
        # then normalise internally to the bounded ``subagents`` batch action.
        action["action"] = "subagents"
        action["subagents"] = [action.get("subagent")]
    if action.get("action") not in {"skill", "subagents", "final"}:
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


def _run_market_evidence_skill(state: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Expose current evidence to the parent trace instead of trusting a report string."""
    from agent_runtime.agents.research_harness import _market_metadata
    from tools.akshare_tools import get_financial_indicator, get_stock_price

    tool = get_stock_price if tool_name == "market-price" else get_financial_indicator
    content = str(tool.invoke({"symbol": state["stock_code"]}))
    metadata = _market_metadata(content, tool_name)
    return {
        "ok": not content.startswith("[TOOL_ERROR]"),
        "content": content,
        "source_kind": "market_evidence",
        "tool_metadata": metadata["fields"],
        "freshness": metadata["freshness"],
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
    if skill_name in {"market-price", "financial-indicators"}:
        return _run_market_evidence_skill(state, skill_name)
    raise ValueError(f"unsupported skill: {skill_name}")


def _normalise_subagent_result(value: SubagentResult | dict[str, Any], name: str) -> dict[str, Any]:
    """Keep test-injected executors and registry results on one typed contract."""
    if isinstance(value, SubagentResult):
        return value.to_dict()
    if not isinstance(value, dict):
        raise TypeError(f"subagent {name} returned {type(value).__name__}, expected SubagentResult or dict")
    return {
        "subagent": str(value.get("subagent") or name),
        "ok": bool(value.get("ok")),
        "content": str(value.get("content") or ""),
        "updates": dict(value.get("updates") or {}),
        "citations": list(value.get("citations") or []),
        "source_kind": str(value.get("source_kind") or "analyst_report"),
        "status": str(value.get("status") or ("completed" if value.get("ok") else "failed")),
        "trace": dict(value.get("trace") or {}),
    }


def _spawn_subagents(
    names: list[str],
    *,
    state: dict[str, Any],
    granted: set[str],
    registry: SubagentRegistry,
    executor: Callable[..., SubagentResult | dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Create isolated logical child runs from a registry-approved allowlist."""
    if executor is not None:
        return [
            _normalise_subagent_result(
                executor(name, state=state, granted=granted),
                name,
            )
            for name in names
        ]
    task = SubagentTask(
        stock_code=state["stock_code"],
        request_query=str(state.get("analysis_query") or state["stock_code"]),
        session_id=state.get("session_id"),
        document_evidence=str(state.get("user_doc_context") or ""),
    )
    return [
        _normalise_subagent_result(result, result.subagent)
        for result in registry.spawn_many(names, task, granted_permissions=granted)
    ]


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
    subagent_executor: Callable[..., SubagentResult | dict[str, Any]] | None = None,
    subagent_registry_instance: SubagentRegistry | None = None,
) -> dict[str, Any]:
    """Run a bounded parent loop with allowlisted Skills and specialist subagents."""
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
    active_subagent_registry = subagent_registry_instance or subagent_registry
    available_subagents = active_subagent_registry.list_available(
        granted_permissions=granted,
        has_session_document=bool(state.get("session_id")),
    )
    requested_focus = state.get("analyst_focus") or "all"
    if requested_focus in _ALLOWED_FOCUSES:
        permitted_names = {f"{requested_focus}-researcher", "evidence-reviewer"}
        available_subagents = [
            item for item in available_subagents if item["name"] in permitted_names
        ]
    observations: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    executed: set[str] = set()
    executed_subagents: set[str] = set()
    executor = skill_executor or _execute_skill
    retry_budget = RetryBudget()
    model_failures: list[dict[str, Any]] = []
    planner_unavailable = False
    task_plan = state.get("task_plan") if isinstance(state.get("task_plan"), dict) else {}
    task_by_id = {
        task.get("task_id"): task for task in task_plan.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }
    task_status = {
        task_id: (
            "awaiting_confirmation" if task.get("requires_confirmation")
            else task.get("planned_status", "pending") if task.get("planned_status") != "runnable"
            else "pending"
        )
        for task_id, task in task_by_id.items()
    }

    def bound_tasks(skill_name: str) -> list[str]:
        return [
            task_id for task_id, task in task_by_id.items()
            if skill_name in task.get("skill_binding", [])
        ]

    def ready_bound_tasks(skill_name: str) -> list[str]:
        ready = []
        for task_id in bound_tasks(skill_name):
            task = task_by_id[task_id]
            if task_status.get(task_id) != "pending":
                continue
            if all(task_status.get(dependency) == "succeeded" for dependency in task.get("depends_on", [])):
                ready.append(task_id)
        return ready

    def mark_tasks(task_ids: list[str], ok: bool) -> None:
        for task_id in task_ids:
            task_status[task_id] = "succeeded" if ok else "failed"

    task_summary = [
        {
            "task_id": task.get("task_id"),
            "intent": task.get("intent"),
            "depends_on": task.get("depends_on", []),
            "skill_binding": task.get("skill_binding", []),
            "planned_status": task.get("planned_status"),
        }
        for task in task_plan.get("tasks", [])
        if isinstance(task, dict)
    ]
    if task_summary:
        trace.append({"event": "task_plan_received", "tasks": task_summary})

    for step in range(MAX_AGENT_STEPS):
        compacted, compacted_changed = compact_tool_observations(observations)
        if compacted_changed:
            trace.append({"step": step + 1, "event": "microcompact"})
        prompt = f"""You are the parent planner of a governed A-share research agent. Return JSON only.
You may choose read-only infrastructure Skills, one or more registered specialist Subagents,
or finish when evidence is sufficient. Specialist analysis should prefer Subagents; the legacy
analysis Skill remains available only for compatibility. Other Skills cover document retrieval,
backtesting and approved operational memory.
Allowed Skills: {json.dumps(available, ensure_ascii=False)}
Allowed Subagents: {json.dumps(available_subagents, ensure_ascii=False)}
Return exactly one of:
{{"action":"skill","skill":"one allowed name","arguments":{{}},"reason":"..."}}
{{"action":"subagents","subagents":["one or more allowed names"],"reason":"..."}}
{{"action":"final","reason":"..."}}
The stock code is server-bound to {state['stock_code']}; never request another code.
Only choose backtest when the user requested it or it materially resolves a stated uncertainty.
Before finalising an investment conclusion, obtain at least one traceable current
market-price/financial observation or page-cited session-document observation.
Do not choose evidence-reviewer in the same batch as another subagent: retrieve document evidence
first, then decide whether a specialist should consume it in the next step.
Current request: {state.get('analysis_query') or state['stock_code']}
Requested analysis focus: {state.get('analyst_focus') or 'all'}
Server-derived task DAG: {json.dumps(task_summary, ensure_ascii=False)}
Task execution status: {json.dumps(task_status, ensure_ascii=False)}
Complete every runnable task whose dependencies are satisfied. Do not execute a task marked
awaiting_confirmation or route_to_dedicated_endpoint; never invent a tool outside the allowlist.
Failed tools expose error_type/retryable/next_action. Do not retry non-retryable failures; either
use a safe alternative, wait for reauthorization, or preserve the evidence gap in the final draft.
Runtime context (rules and session data; not market evidence): {state.get('agent_context') or ''}
Previous observations: {json.dumps(compacted, ensure_ascii=False)}"""
        try:
            action = _parse_action(_model_content(planner_llm, prompt))
        except Exception as exc:
            failure = classify_model_failure(exc)
            model_failures.append(failure.to_dict())
            trace.append({
                "step": step + 1,
                "event": "planner_model_unavailable",
                "model_failure": failure.to_dict(),
            })
            planner_unavailable = True
            break
        if not action:
            trace.append({"step": step + 1, "event": "invalid_planner_output"})
            break
        if action["action"] == "final":
            trace.append({"step": step + 1, "event": "final", "reason": action.get("reason", "")})
            break

        if action["action"] == "subagents":
            raw_names = action.get("subagents")
            if not isinstance(raw_names, list):
                trace.append({"step": step + 1, "event": "invalid_subagent_request"})
                break
            names = list(dict.fromkeys(str(name) for name in raw_names if str(name)))
            allowed_names = {item["name"] for item in available_subagents}
            if not names or any(name not in allowed_names for name in names):
                trace.append({
                    "step": step + 1,
                    "event": "subagent_denied",
                    "subagents": names,
                })
                break
            if "evidence-reviewer" in names and len(names) > 1:
                trace.append({
                    "step": step + 1,
                    "event": "subagent_denied",
                    "subagents": names,
                    "reason": "evidence-reviewer must complete before other specialists",
                })
                break
            names = [name for name in names if name not in executed_subagents]
            if not names:
                trace.append({"step": step + 1, "event": "duplicate_subagent_skipped"})
                continue
            analysis_task_ids = ready_bound_tasks("analysis")
            if bound_tasks("analysis") and not analysis_task_ids:
                trace.append({
                    "step": step + 1,
                    "event": "task_dependency_blocked",
                    "skill": "analysis",
                    "task_status": dict(task_status),
                })
                continue
            executed_subagents.update(names)
            try:
                results = _spawn_subagents(
                    names,
                    state=state,
                    granted=granted,
                    registry=active_subagent_registry,
                    executor=subagent_executor,
                )
                for result in results:
                    state.update(result["updates"])
                    result_ref = persist_tool_result(
                        tool=f"subagent:{result['subagent']}",
                        content=result["content"],
                        source_kind=result["source_kind"],
                        citations=result["citations"],
                    )
                    observation = {
                        "tool": f"subagent:{result['subagent']}",
                        "subagent": result["subagent"],
                        "ok": result["ok"],
                        "content": _trim(result["content"]),
                        "citations": result["citations"],
                        "source_kind": result["source_kind"],
                        "result_ref": result_ref,
                        "subagent_status": result["status"],
                        "subagent_trace": result["trace"],
                    }
                    observations.append(observation)
                    trace.append({
                        "step": step + 1,
                        "event": "subagent_result",
                        "subagent": result["subagent"],
                        "ok": result["ok"],
                        "status": result["status"],
                        "latency_ms": result["trace"].get("latency_ms"),
                        "result_ref": result_ref,
                    })
                if analysis_task_ids:
                    mark_tasks(analysis_task_ids, all(bool(item.get("ok")) for item in results))
            except Exception as exc:
                if analysis_task_ids:
                    mark_tasks(analysis_task_ids, False)
                trace.append({
                    "step": step + 1,
                    "event": "subagent_error",
                    "subagents": names,
                    "error": str(exc)[:300],
                })
            continue

        skill_name = str(action.get("skill") or "")
        spec = _SKILL_CATALOG.get(skill_name)
        if not spec or skill_name not in {item["name"] for item in available}:
            trace.append({"step": step + 1, "event": "skill_denied", "skill": skill_name})
            break
        task_ids = ready_bound_tasks(skill_name)
        if bound_tasks(skill_name) and not task_ids:
            trace.append({
                "step": step + 1,
                "event": "task_dependency_blocked",
                "skill": skill_name,
                "task_status": dict(task_status),
            })
            continue
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
            arguments = dict(action.get("arguments") or {})
            result = invoke_with_failure_policy(
                skill_name,
                lambda: executor(
                    skill_name,
                    state=state,
                    arguments=arguments,
                    granted=granted,
                ),
                cache_key=json.dumps([skill_name, state["stock_code"], arguments], ensure_ascii=False, sort_keys=True),
                retry_budget=retry_budget,
                cache=DEFAULT_TOOL_CACHE,
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
                "tool_failure": result.get("tool_failure"),
                "degraded": bool(result.get("degraded")),
            }
            observations.append(observation)
            trace.append({
                "step": step + 1,
                "event": "skill_result",
                "skill": skill_name,
                "ok": observation["ok"],
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "result_ref": result_ref,
                "attempts": result.get("attempts", 1),
                "retry_trace": result.get("retry_trace", []),
                "tool_failure": result.get("tool_failure"),
                "circuit_state": result.get("circuit_state", "closed"),
                "degraded": bool(result.get("degraded")),
            })
            if task_ids:
                mark_tasks(task_ids, observation["ok"])
        except Exception as exc:
            if task_ids:
                mark_tasks(task_ids, False)
            failure = classify_tool_failure(exc)
            observations.append({
                "tool": skill_name,
                "ok": False,
                "content": f"[TOOL_ERROR] error_type={failure.error_type.value} message={failure.message}",
                "tool_failure": failure.to_dict(),
            })
            trace.append({
                "step": step + 1,
                "event": "skill_error",
                "skill": skill_name,
                "tool_failure": failure.to_dict(),
            })
    else:
        trace.append({"step": MAX_AGENT_STEPS, "event": "budget_exhausted"})

    # A malformed or overly conservative planner must not make the standard
    # analyse endpoint return an empty report. This is a safe, bounded fallback.
    if not planner_unavailable and not any(state.get(f"{focus}_report") for focus in _ALLOWED_FOCUSES):
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
        fallback_task_ids = ready_bound_tasks("analysis")
        if fallback_task_ids:
            mark_tasks(fallback_task_ids, bool(fallback["ok"]))

    for task_id, task in task_by_id.items():
        if task_status[task_id] != "pending":
            continue
        task_status[task_id] = (
            "blocked_dependency" if task.get("depends_on") else "incomplete"
        )
    if task_by_id:
        trace.append({"event": "task_plan_status", "task_status": dict(task_status)})
    state["task_status"] = task_status
    state["reliability_summary"] = retry_budget.summary()
    trace.append({"event": "retry_budget", **state["reliability_summary"]})
    # Persist the failure state before Validation's early-abort branch. This
    # keeps a model outage auditable instead of returning an unadorned abort.
    state.update({
        "research_evidence": observations,
        "evidence_cards": build_evidence_cards(observations),
        "agent_trace": trace,
        "model_failures": model_failures,
    })

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

    try:
        report = _research_report(
            stock_code=state["stock_code"],
            snapshot=state["context_snapshot"],
            observations=observations,
            final_llm=final_llm,
        )
    except Exception as exc:
        failure = classify_model_failure(exc)
        model_failures.append(failure.to_dict())
        trace.append({"event": "research_model_unavailable", "model_failure": failure.to_dict()})
        state["final_decision"] = "[PUBLISH_BLOCKED] 模型服务不可用，未生成投资结论。"
        state.update(evaluate_output_gate(state))
        state["human_review_required"] = True
        return state
    state.update({
        "bull_argument": report,
        "bear_argument": report,
        "research_evidence": observations,
        "evidence_cards": build_evidence_cards(observations),
        "agent_trace": trace,
    })
    try:
        state.update(trader_node(state))
    except Exception as exc:
        failure = classify_model_failure(exc)
        model_failures.append(failure.to_dict())
        trace.append({"event": "trader_model_unavailable", "model_failure": failure.to_dict()})
        state["final_decision"] = "[PUBLISH_BLOCKED] 模型服务不可用，未生成投资结论。"
    state.update(output_gate_node(state))
    # Keep this direct evaluation as a final defensive invariant if callers
    # replace output_gate_node with an integration adapter.
    state.update(evaluate_output_gate(state))
    incomplete = [
        task_id for task_id, status in task_status.items()
        if status in {
            "failed", "blocked_dependency", "blocked_missing_slots", "route_to_dedicated_endpoint", "incomplete",
        }
    ]
    if incomplete:
        state["publish_status"] = "blocked"
        state["human_review_required"] = True
        state["publish_reasons"] = list(dict.fromkeys([
            *(state.get("publish_reasons") or []),
            f"required task plan incomplete: {', '.join(incomplete)}",
        ]))
    return state
