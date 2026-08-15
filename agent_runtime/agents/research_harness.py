"""Bounded agentic loop for evidence research inside the fixed trading graph.

The model chooses the next read-only tool, but this harness owns permission
checks, stock-code binding, result limits, iteration limits and the audit trace.
It is intentionally not used for final trading decisions.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

from agent_runtime.context.compaction import (
    compact_tool_observations,
    emergency_tool_observations,
    is_context_overflow_error,
    persist_tool_result,
)
from agent_runtime.reliability import (
    DEFAULT_TOOL_CACHE,
    RetryBudget,
    classify_model_failure,
    classify_tool_failure,
    invoke_with_failure_policy,
)
from control_plane.security import SecurityOperation, authorize_operation


MAX_TOOL_CALLS = 3
MAX_TOOL_RESULT_CHARS = 1_800
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)
_TOOL_FIELD = re.compile(r"^([a-z_]+)=(.+)$", re.M)
_DATE = re.compile(r"((?:19|20)\d{2})[-/]?(\d{2})[-/]?(\d{2})")

_TOOL_CATALOG = {
    "document-rag": {
        "permission": "document:read",
        "description": "Search session-scoped uploaded-document evidence. Arguments: {query}",
    },
    "market-price": {
        "permission": "market:read",
        "description": "Get current price and basic market verification. No arguments needed.",
    },
    "market-history": {
        "permission": "market:read",
        "description": "Get bounded daily K-line history for deterministic price evidence. No arguments needed.",
    },
    "financial-indicators": {
        "permission": "market:read",
        "description": "Get financial indicators for the requested stock. No arguments needed.",
    },
    "stock-news": {
        "permission": "market:read",
        "description": "Get recent stock news. No arguments needed.",
    },
    "memory-search": {
        "permission": "memory:read",
        "description": "Search approved Agent-memory Markdown for reusable operating knowledge. Arguments: {query}",
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
    if action.get("action") not in {"tool", "final"}:
        return None
    return action


def _trim(value: Any) -> str:
    text = str(value or "")
    return text if len(text) <= MAX_TOOL_RESULT_CHARS else text[:MAX_TOOL_RESULT_CHARS] + "\n[tool result truncated]"


def _invoke_with_reactive_compaction(
    llm: Any,
    build_prompt: Callable[[list[dict[str, Any]]], str],
    observations: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    *,
    stage: str,
) -> str:
    """Use a compacted evidence view, retrying once after a context overflow."""

    prompt_observations, compacted = compact_tool_observations(observations)
    if compacted:
        trace.append({"event": "microcompact", "stage": stage, "observation_count": len(observations)})
    try:
        return getattr(llm.invoke(build_prompt(prompt_observations)), "content", "")
    except Exception as exc:
        if not is_context_overflow_error(exc):
            raise
        trace.append({
            "event": "reactive_compact_retry",
            "stage": stage,
            "reason": "provider_context_overflow",
        })
        emergency = emergency_tool_observations(observations)
        return getattr(llm.invoke(build_prompt(emergency)), "content", "")


def _market_metadata(content: str, tool_name: str) -> dict[str, Any]:
    """Expose timestamp and reporting-period facts beside raw tool text."""

    fields = {key: value.strip() for key, value in _TOOL_FIELD.findall(content or "")}
    freshness: dict[str, Any] = {
        "status": "unknown",
        "retrieved_at": fields.get("retrieved_at"),
        "data_source": fields.get("data_source"),
    }
    if tool_name != "financial-indicators":
        freshness["status"] = "retrieved" if freshness["retrieved_at"] else "missing_retrieval_time"
        return {"fields": fields, "freshness": freshness}

    report_period = fields.get("report_period")
    freshness.update({
        "report_period": report_period,
        "report_period_raw": fields.get("report_period_raw"),
        "report_period_source_field": fields.get("report_period_source_field"),
        "report_type": fields.get("report_type"),
    })
    match = _DATE.search(report_period or "")
    if not match:
        freshness["status"] = "missing_report_period"
        return {"fields": fields, "freshness": freshness}

    from datetime import date

    report_date = date(*map(int, match.groups()))
    age_days = (date.today() - report_date).days
    freshness.update({"report_date": report_date.isoformat(), "age_days": age_days})
    freshness["status"] = "stale" if age_days > 540 else "reported_period"
    freshness["usable_for_current_conclusion"] = freshness["status"] == "reported_period"
    return {"fields": fields, "freshness": freshness}


def _default_executor(
    tool_name: str,
    *,
    stock_code: str,
    session_id: str | None,
    query: str,
    granted_permissions: set[str],
) -> dict[str, Any]:
    """Execute only registry-approved read-only skills or server-owned tools."""
    if tool_name == "document-rag":
        if not session_id:
            return {"ok": False, "error": "no session document is available"}
        from agent_runtime.skills.registry import skill_registry

        result = skill_registry.execute(
            "document-rag",
            granted_permissions=granted_permissions,
            session_id=session_id,
            query=query,
        )
        return {"ok": True, "content": result.get("context", ""), "citations": result.get("citations", [])}

    if tool_name == "memory-search":
        # This store contains human-approved operating knowledge only. It is
        # neither live market evidence nor a substitute for document citations.
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
        content = "\n\n".join(
            f"[{item['evidence_id']}]\n{item['content']}" for item in matches
        )
        return {
            "ok": True,
            "content": content or "[no approved memory matched]",
            "citations": citations,
            "source_kind": "operational_memory",
        }

    from tools.akshare_tools import get_financial_indicator, get_stock_history, get_stock_news, get_stock_price

    tools = {
        "market-price": get_stock_price,
        "market-history": get_stock_history,
        "financial-indicators": get_financial_indicator,
        "stock-news": get_stock_news,
    }
    tool = tools[tool_name]
    arguments = {"symbol": stock_code}
    if tool_name == "market-history":
        arguments["days"] = 30
    content = tool.invoke(arguments)
    metadata = _market_metadata(content, tool_name)
    return {
        "ok": not str(content).startswith("[TOOL_ERROR]"),
        "content": content,
        "citations": [],
        "source_kind": "market_evidence",
        "tool_metadata": metadata["fields"],
        "freshness": metadata["freshness"],
    }


def run_research_harness(
    *,
    stock_code: str,
    snapshot: dict[str, Any],
    session_id: str | None = None,
    request_query: str = "",
    runtime_context: str = "",
    granted_permissions: set[str] | None = None,
    planner_llm: Any | None = None,
    final_llm: Any | None = None,
    tool_executor: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a bounded evidence loop and return a final research report plus trace."""
    if planner_llm is None or final_llm is None:
        from config.llm_config import deep_llm, quick_llm

        planner_llm = planner_llm or quick_llm
        final_llm = final_llm or deep_llm
    granted = granted_permissions or {"document:read", "market:read", "memory:read"}
    executor = tool_executor or _default_executor
    available = [
        {"name": name, "description": spec["description"]}
        for name, spec in _TOOL_CATALOG.items()
        if spec["permission"] in granted and (name != "document-rag" or session_id)
    ]
    trace: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    executed_tool_requests: set[tuple[str, str]] = set()
    retry_budget = RetryBudget()
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2)

    def model_abort(stage: str, exc: Exception) -> dict[str, Any]:
        failure = classify_model_failure(exc)
        trace.append({
            "event": "model_unavailable",
            "stage": stage,
            "model_failure": failure.to_dict(),
        })
        return {
            "report": (
                "[RESEARCH_ABORT] 模型服务不可用，未生成研究摘要；"
                f"error_type={failure.error_type.value}"
            ),
            "trace": trace,
            "observations": observations,
            "retry_budget": retry_budget.summary(),
            "model_failures": [failure.to_dict()],
        }

    for step in range(MAX_TOOL_CALLS):
        def planner_prompt(previous_observations: list[dict[str, Any]]) -> str:
            return f"""You are an evidence-planning agent in a read-only A-share research workflow.
Choose whether more evidence is necessary. Return JSON only.
Allowed tools: {json.dumps(available, ensure_ascii=False)}
Output one of:
{{"action":"tool","tool":"one allowed name","arguments":{{"query":"optional document query"}},"reason":"..."}}
{{"action":"final","reason":"existing evidence is sufficient or no safe tool can help"}}
The stock code is server-bound to {stock_code}; never request another code.
Tool failures include ``error_type``, ``retryable`` and ``next_action``. Never repeat a
non-retryable tool request: repair only server-allowed parameters, request reauthorization,
choose another safe tool, or finish with an evidence gap.
Current request: {request_query}
Runtime context (rules, selected skills and session information; session data is not evidence):
{runtime_context}
Structured evidence snapshot:
{snapshot_text}
Previous tool observations:
{json.dumps(previous_observations, ensure_ascii=False)}"""

        try:
            raw = _invoke_with_reactive_compaction(
                planner_llm, planner_prompt, observations, trace, stage="planner"
            )
        except Exception as exc:
            return model_abort("planner", exc)
        action = _parse_action(raw)
        if not action:
            trace.append({"step": step + 1, "event": "invalid_planner_output", "raw": _trim(raw)})
            break
        if action["action"] == "final":
            trace.append({"step": step + 1, "event": "final", "reason": action.get("reason", "")})
            break

        tool_name = action.get("tool", "")
        spec = _TOOL_CATALOG.get(tool_name)
        if not spec or spec["permission"] not in granted or tool_name not in {item["name"] for item in available}:
            trace.append({"step": step + 1, "event": "tool_denied", "tool": tool_name})
            break

        tool_query = str(action.get("arguments", {}).get("query") or request_query).strip()
        request_key = (tool_name, tool_query)
        if request_key in executed_tool_requests:
            trace.append({"step": step + 1, "event": "duplicate_tool_skipped", "tool": tool_name})
            continue
        executed_tool_requests.add(request_key)

        permission_tool = {
            "document-rag": "document:read",
            "memory-search": "memory:read",
        }.get(tool_name, "market:read")
        try:
            authorize_operation(
                SecurityOperation(
                    tool=permission_tool,
                    target=tool_name,
                    actor_id=None,
                    session_id=session_id,
                ),
                mode="auto",
            )
        except PermissionError:
            failure = classify_tool_failure(PermissionError("permission denied"))
            trace.append({
                "step": step + 1,
                "event": "permission_denied",
                "tool": tool_name,
                "tool_failure": failure.to_dict(),
            })
            observations.append({
                "tool": tool_name,
                "ok": False,
                "content": f"[TOOL_ERROR] error_type={failure.error_type.value} message={failure.message}",
                "tool_failure": failure.to_dict(),
            })
            break

        started = time.monotonic()
        result = invoke_with_failure_policy(
            tool_name,
            lambda: executor(
                tool_name,
                stock_code=stock_code,
                session_id=session_id,
                query=tool_query,
                granted_permissions=granted,
            ),
            cache_key=json.dumps([tool_name, stock_code, session_id, tool_query], ensure_ascii=False),
            retry_budget=retry_budget,
            cache=DEFAULT_TOOL_CACHE,
        )
        content = _trim(result.get("content", ""))
        source_kind = result.get("source_kind", "evidence")
        result_ref = persist_tool_result(
            tool=tool_name,
            content=str(result.get("content", "")),
            source_kind=source_kind,
            citations=result.get("citations", []),
            stock_code=stock_code,
        )
        event = {
            "step": step + 1,
            "event": "tool_result",
            "tool": tool_name,
            "ok": bool(result.get("ok")),
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "citations": result.get("citations", []),
            "freshness": result.get("freshness", {}),
            "result_ref": result_ref,
            "attempts": result.get("attempts", 1),
            "retry_trace": result.get("retry_trace", []),
            "tool_failure": result.get("tool_failure"),
            "circuit_state": result.get("circuit_state", "closed"),
            "degraded": bool(result.get("degraded")),
        }
        trace.append(event)
        observations.append({
            "tool": tool_name,
            "ok": event["ok"],
            "content": content,
            "citations": event["citations"],
            "source_kind": source_kind,
            "result_ref": result_ref,
            "tool_metadata": result.get("tool_metadata", {}),
            "freshness": event["freshness"],
            "tool_failure": event["tool_failure"],
            "degraded": event["degraded"],
        })
    else:
        trace.append({"step": MAX_TOOL_CALLS, "event": "budget_exhausted"})

    def final_prompt(compacted_observations: list[dict[str, Any]]) -> str:
        return f"""You are the research node in an A-share workflow. Use only the structured
snapshot and tool observations below. Do not invent data. State evidence conflicts,
missing evidence and tool failures explicitly. Return concise bull arguments, bear
arguments and an overall stance; this is research evidence, not a trade instruction.
Any observation whose source_kind is operational_memory is reusable operating
guidance only: it must not be used to assert current company facts, prices, financial
metrics or news. Those claims require current market or document evidence.
Any observation whose freshness.status is stale, missing_report_period or
missing_retrieval_time is not current financial evidence. State that limitation
instead of using its numbers as a present-tense claim.

Snapshot:
{snapshot_text}
Tool observations:
{json.dumps(compacted_observations, ensure_ascii=False)}"""
    try:
        report = _invoke_with_reactive_compaction(
            final_llm, final_prompt, observations, trace, stage="final"
        )
    except Exception as exc:
        return model_abort("final", exc)
    return {
        "report": report,
        "trace": trace,
        "observations": observations,
        "retry_budget": retry_budget.summary(),
    }
