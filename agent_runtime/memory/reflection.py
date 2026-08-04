"""Turn measured failures into reviewable experience candidates, never approvals."""

from __future__ import annotations

from typing import Any

from agent_runtime.memory.candidates import MemoryCandidate, create_candidate


def candidate_from_bad_case(case: dict[str, Any], *, requested_by: str, source_run_id: str | None = None) -> MemoryCandidate:
    title = f"Bad Case: {str(case.get('failure_type') or 'unknown').strip()[:80]}"
    content = (
        f"Observed failure: {case.get('observed', '')}\n\n"
        f"Expected behavior: {case.get('expected', '')}\n\n"
        f"Root-cause hypothesis: {case.get('root_cause', 'pending human review')}\n\n"
        "This is a candidate lesson. It must not be treated as a market fact or enabled until reviewed."
    )
    return create_candidate(title=title, content=content, category="governance", source_run_id=source_run_id, requested_by=requested_by)


def candidate_from_backtest_deviation(
    *, expected: dict[str, Any], actual: dict[str, Any], requested_by: str, source_run_id: str | None = None,
) -> MemoryCandidate:
    content = (
        f"Expected backtest constraints: {expected}\n\n"
        f"Observed backtest metrics: {actual}\n\n"
        "Review whether the deviation is caused by data range, execution assumptions, strategy logic, or a false research claim."
    )
    return create_candidate(title="Backtest deviation review", content=content, category="research", source_run_id=source_run_id, requested_by=requested_by)


def candidate_from_postmortem(title: str, content: str, *, requested_by: str, source_run_id: str | None = None) -> MemoryCandidate:
    return create_candidate(title=title, content=content, category="operations", source_run_id=source_run_id, requested_by=requested_by)
