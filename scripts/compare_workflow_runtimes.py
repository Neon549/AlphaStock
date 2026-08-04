#!/usr/bin/env python3
"""Compare the LangGraph and explicit-Python investment runtimes.

The comparison is intentionally *not* an exact string diff: each runtime asks
the model independently, so wording may differ.  It records the artifacts and
compares stable workflow contracts such as terminal publication status,
human-review requirement and analyst/evidence availability.

Examples:
  python scripts/compare_workflow_runtimes.py 600519
  python scripts/compare_workflow_runtimes.py 600519 --execute --output runtime/reports/runtime-compare-600519.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable


REQUIRED_MODULES = ("langgraph", "langchain_openai", "akshare", "psycopg2")
_DECISION = re.compile(r"决策\s*[:：]\s*([^\n\r]+)")
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ``python scripts/...`` otherwise puts only scripts/ on sys.path.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The legacy analysts emit Chinese and emoji diagnostics.  Windows consoles may
# otherwise expose a GBK stdout stream and abort a valid run while printing.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def preflight() -> dict[str, Any]:
    env_file = PROJECT_ROOT / ".env"
    env_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    return {
        "project_root": str(PROJECT_ROOT),
        "modules": {name: importlib.util.find_spec(name) is not None for name in REQUIRED_MODULES},
        "env_file_present": env_file.exists(),
        "deepseek_key_configured": bool(os.getenv("DEEPSEEK_API_KEY"))
        or bool(re.search(r"^DEEPSEEK_API_KEY\s*=\s*[^\s#]+", env_text, re.MULTILINE)),
    }


def ready_to_execute(status: dict[str, Any]) -> bool:
    return all(status["modules"].values()) and status["deepseek_key_configured"]


def _decision_category(result: dict[str, Any]) -> str | None:
    text = str(result.get("draft_decision") or result.get("final_decision") or "")
    matched = _DECISION.search(text)
    return matched.group(1).strip() if matched else None


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    reports = {
        name: str(result.get(f"{name}_report") or "")
        for name in ("fundamental", "technical", "sentiment")
    }
    snapshot = result.get("context_snapshot") or {}
    return {
        "publish_status": result.get("publish_status"),
        "human_review_required": result.get("human_review_required"),
        # This remains an artifact for a reviewer, not a pass/fail field: two
        # independent model calls can choose different valid draft wording.
        "decision_category": _decision_category(result),
        "analyst_status": {
            name: "ok" if value.startswith("[ANALYSIS_OK]") else "skipped" if value.startswith("[SKIPPED]") else "other"
            for name, value in reports.items()
        },
        "research_evidence_count": len(result.get("research_evidence") or []),
        "has_draft": bool(result.get("draft_decision") or result.get("final_decision")),
        "risk_contract": {
            "has_risk_assessment": bool(result.get("risk_assessment")),
            "unresolved_risk_count": len(snapshot.get("unresolved_risks") or []),
            "tool_error_count": len(snapshot.get("tool_errors") or []),
            "replan_required": bool(result.get("replan_required")),
        },
    }


def compare_contracts(langgraph: dict[str, Any], python: dict[str, Any]) -> dict[str, Any]:
    left, right = summarize_result(langgraph), summarize_result(python)
    fields = (
        "publish_status",
        "human_review_required",
        "analyst_status",
        "research_evidence_count",
        "has_draft",
        "risk_contract",
    )
    differences = {
        field: {"langgraph": left[field], "python": right[field]}
        for field in fields
        if left[field] != right[field]
    }
    return {
        "langgraph": left,
        "python": right,
        "contract_match": not differences,
        "differences": differences,
        "non_blocking_observations": {
            "langgraph_decision_category": left["decision_category"],
            "python_decision_category": right["decision_category"],
        },
        "note": "Generated decision wording/category is observed but never used as a runtime-contract pass/fail field.",
    }


def summarize_backtest_result(result: dict[str, Any]) -> dict[str, Any]:
    request = result.get("backtest_request") or {}
    report = str(result.get("backtest_report") or "")
    return {
        "backtest_status": "tool_error" if "[TOOL_ERROR]" in report else "ok",
        "has_summary": bool(result.get("backtest_summary")),
        "optimizer_ran": bool(result.get("backtest_optimizer_ran")),
        "optimizer_skipped": bool(result.get("backtest_optimizer_skipped")),
        "optimizer_error": bool(result.get("backtest_optimizer_error")),
        "request_contract": {
            key: request.get(key)
            for key in ("stock_code", "strategy", "start_date", "end_date", "initial_cash")
        },
    }


def compare_backtest_contracts(langgraph: dict[str, Any], python: dict[str, Any]) -> dict[str, Any]:
    left, right = summarize_backtest_result(langgraph), summarize_backtest_result(python)
    differences = {
        field: {"langgraph": left[field], "python": right[field]}
        for field in left
        if left[field] != right[field]
    }
    return {
        "langgraph": left,
        "python": right,
        "contract_match": not differences,
        "differences": differences,
        "note": "Backtest report wording and performance values are retained as artifacts, not string-compared.",
    }


def _invoke(runtime_name: str, runner: Callable[..., dict[str, Any]], stock_code: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = runner(stock_code, **kwargs)
        return {"runtime": runtime_name, "elapsed_seconds": round(time.perf_counter() - started, 3), "result": result}
    except Exception as exc:  # Preserve an artifact from one failing side instead of hiding it.
        return {"runtime": runtime_name, "elapsed_seconds": round(time.perf_counter() - started, 3), "error": f"{type(exc).__name__}: {exc}"}


def execute(
    stock_code: str,
    *,
    focus: str,
    model: str,
    query: str,
    doc_context: str = "",
    document_citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from agent_runtime.workflows.runtime import LangGraphInvestmentRuntime, PythonInvestmentRuntime

    common_kwargs = {
        "doc_context": doc_context,
        "document_citations": document_citations or [],
        "session_id": None,
        "analysis_query": query,
        "analyst_focus": focus,
        "memory_context": {},
        "agent_context": "",
        "model_profile": model,
    }
    graph_run = _invoke("langgraph", LangGraphInvestmentRuntime().run, stock_code, common_kwargs)
    python_run = _invoke("python", PythonInvestmentRuntime().run, stock_code, common_kwargs)
    output: dict[str, Any] = {"stock_code": stock_code, "input": common_kwargs, "runs": [graph_run, python_run]}
    if "result" in graph_run and "result" in python_run:
        output["comparison"] = compare_contracts(graph_run["result"], python_run["result"])
    else:
        output["comparison"] = {"contract_match": False, "differences": {"execution": "one or both runtimes failed"}}
    return output


def execute_backtest(
    stock_code: str, *, strategy: str, start_date: str, end_date: str, initial_cash: float, model: str
) -> dict[str, Any]:
    from agent_runtime.workflows.runtime import LangGraphBacktestRuntime, PythonBacktestRuntime

    common_kwargs = {
        "strategy": strategy,
        "start_date": start_date,
        "end_date": end_date,
        "initial_cash": initial_cash,
        "model_profile": model,
    }
    graph_run = _invoke("langgraph", LangGraphBacktestRuntime().run, stock_code, common_kwargs.copy())
    python_run = _invoke("python", PythonBacktestRuntime().run, stock_code, common_kwargs.copy())
    output: dict[str, Any] = {
        "stock_code": stock_code,
        "input": common_kwargs,
        "runs": [graph_run, python_run],
    }
    if "result" in graph_run and "result" in python_run:
        output["comparison"] = compare_backtest_contracts(graph_run["result"], python_run["result"])
    else:
        output["comparison"] = {"contract_match": False, "differences": {"execution": "one or both runtimes failed"}}
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare LangGraph and Python investment runtimes")
    parser.add_argument("stock_code", help="six-digit A-share stock code")
    parser.add_argument("--focus", choices=["all", "technical", "fundamental", "sentiment"], default="all")
    parser.add_argument("--model", choices=["fast", "smart", "strong"], default="smart")
    parser.add_argument("--query", help="analysis query supplied to both runs")
    parser.add_argument("--scenario", choices=["investment", "backtest"], default="investment")
    parser.add_argument("--document-context-file", type=Path, help="UTF-8 RAG context supplied identically to both investment runs")
    parser.add_argument("--document-citations-file", type=Path, help="JSON list of citations for --document-context-file")
    parser.add_argument("--strategy", default="kdj_macd")
    parser.add_argument("--start-date", default="20220101")
    parser.add_argument("--end-date", default="20261231")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--execute", action="store_true", help="run both runtimes; this makes external model/data calls twice")
    parser.add_argument("--output", type=Path, help="optional JSON artifact path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status = preflight()
    if not args.execute:
        output: dict[str, Any] = {"mode": "preflight", "ready_to_execute": ready_to_execute(status), **status}
    elif not ready_to_execute(status):
        output = {"mode": "blocked_preflight", "ready_to_execute": False, **status}
    else:
        if args.scenario == "backtest":
            output = execute_backtest(
                args.stock_code,
                strategy=args.strategy,
                start_date=args.start_date,
                end_date=args.end_date,
                initial_cash=args.initial_cash,
                model=args.model,
            )
        else:
            doc_context = args.document_context_file.read_text(encoding="utf-8") if args.document_context_file else ""
            citations = json.loads(args.document_citations_file.read_text(encoding="utf-8")) if args.document_citations_file else []
            if not isinstance(citations, list):
                raise ValueError("document citations file must contain a JSON list")
            output = execute(
                args.stock_code,
                focus=args.focus,
                model=args.model,
                query=args.query or f"分析 {args.stock_code}",
                doc_context=doc_context,
                document_citations=citations,
            )

    text = json.dumps(output, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0 if output.get("mode") != "blocked_preflight" else 2


if __name__ == "__main__":
    raise SystemExit(main())
