"""Build safe, UI-ready evidence cards from audited tool observations."""

from __future__ import annotations

from typing import Any


def build_evidence_cards(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose provenance/freshness without copying a raw financial tool result."""

    cards: list[dict[str, Any]] = []
    for observation in observations:
        if observation.get("tool") != "financial-indicators" or not observation.get("ok"):
            continue
        freshness = observation.get("freshness") or {}
        metadata = observation.get("tool_metadata") or {}
        cards.append({
            "evidence_id": observation.get("result_ref") or "market:financial-indicators",
            "kind": "financial_statement",
            "title": "财务摘要",
            "data_source": freshness.get("data_source") or metadata.get("data_source") or "unknown",
            "retrieved_at": freshness.get("retrieved_at"),
            "report_period": freshness.get("report_period"),
            "report_period_source_field": freshness.get("report_period_source_field"),
            "age_days": freshness.get("age_days"),
            "freshness": freshness.get("status", "unknown"),
            "usable_for_current_conclusion": bool(freshness.get("usable_for_current_conclusion")),
        })
    return cards
