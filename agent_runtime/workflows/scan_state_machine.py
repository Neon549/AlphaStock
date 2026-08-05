"""Bounded Python runtime for the daily signal scan.

This replaces the old LangGraph-only scan graph.  Scanning remains a fixed,
auditable workflow: discover candidates, analyse each candidate, then apply a
deterministic recommendation filter.  It deliberately does not use the open
research Agent Loop because a batch scan must have predictable cost and scope.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable


CandidateAnalyser = Callable[[dict[str, Any]], dict[str, Any]]
CandidateScanner = Callable[[str | None, str, int], Iterable[dict[str, Any]]]


def _default_scanner(base_start: str | None, strategy: str, top_n: int):
    from backtest.signal_scanner import scan_today

    return scan_today(top_n=top_n, base_start=base_start, strategy=strategy)


def _default_analyser(candidate: dict[str, Any]) -> dict[str, Any]:
    from agent_runtime.agents.fundamental_analyst import run_fundamental_analysis
    from agent_runtime.agents.sentiment_analyst import run_sentiment_analysis
    from agent_runtime.agents.technical_analyst import run_technical_analysis
    from agent_runtime.agents.validator import run_validator

    stock_code = str(candidate["code"])
    fundamental = run_fundamental_analysis(stock_code)
    technical = run_technical_analysis(stock_code)
    sentiment = run_sentiment_analysis(stock_code)
    validation = run_validator(
        stock_code=stock_code,
        fundamental_report=fundamental,
        technical_report=technical,
        sentiment_report=sentiment,
        researcher_analysis="",
    )
    return {
        **candidate,
        "decision": validation.get("decision", "观望"),
        "confidence": validation.get("confidence", "低"),
        "consistent": validation.get("consistent", False),
        "report": validation.get("report", ""),
    }


def _is_recommendation(result: dict[str, Any]) -> bool:
    # Validator outputs may contain historical aliases; keeping them here
    # makes the safety filter explicit rather than hiding it in an LLM prompt.
    return result.get("decision") in {"买入", "关注", "BUY"} and result.get("confidence") in {
        "高",
        "中",
        "HIGH",
        "MEDIUM",
    }


def _j_value(result: dict[str, Any]) -> float:
    try:
        return float(result.get("j"))
    except (TypeError, ValueError):
        return float("inf")


def run_daily_scan(
    base_start: str | None = None,
    strategy: str = "all",
    top_n: int = 5,
    *,
    scanner: CandidateScanner | None = None,
    analyser: CandidateAnalyser | None = None,
) -> dict[str, Any]:
    """Run candidate discovery and bounded three-aspect validation.

    Per-candidate failures are captured in ``analysis_errors`` instead of
    aborting the whole batch, matching the former graph's graceful behaviour.
    """

    scan = scanner or _default_scanner
    analyse = analyser or _default_analyser
    candidates = list(scan(base_start, strategy, top_n) or [])
    analysis_results: list[dict[str, Any]] = []
    analysis_errors: list[dict[str, str]] = []

    for candidate in candidates:
        try:
            analysis_results.append(analyse(candidate))
        except Exception as exc:  # A single stock must not invalidate the scan.
            analysis_errors.append(
                {
                    "stock_code": str(candidate.get("code", "")),
                    "reason": str(exc),
                }
            )

    recommendations = [result for result in analysis_results if _is_recommendation(result)]
    recommendations.sort(key=_j_value)
    return {
        "candidates": candidates,
        "analysis_results": analysis_results,
        "analysis_errors": analysis_errors,
        "final_recommendations": recommendations,
        "runtime": "python_state_machine",
    }
