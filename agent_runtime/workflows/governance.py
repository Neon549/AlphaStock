"""Deterministic governance controls for the analysis workflow.

These checks deliberately do not ask an LLM to judge its own output.  They
control execution scope and decide whether an LLM-generated report may be
published, must be reviewed, or must be blocked.
"""

from __future__ import annotations

import re
from typing import Any


_STOCK_CODE = re.compile(r"^\d{6}$")
_UNSUPPORTED_CLAIMS = (
    "guaranteed return",
    "risk-free",
    "\u7a33\u8d5a",
    "\u4fdd\u8bc1\u6536\u76ca",
    "\u5fc5\u6da8",
    "\u4e00\u5b9a\u4e0a\u6da8",
)


def validate_analysis_scope(stock_code: str, analyst_focus: str, doc_context: str) -> dict[str, Any]:
    """Return an allow/deny decision before any data tool is invoked."""
    violations: list[str] = []
    if not _STOCK_CODE.fullmatch(stock_code or ""):
        violations.append("stock_code must be a six-digit A-share code")
    if analyst_focus not in {"all", "fundamental", "technical", "sentiment"}:
        violations.append("analyst_focus is outside the allowed scope")
    # Prevent an uploaded document from becoming an unbounded prompt/tool input.
    if len(doc_context or "") > 30_000:
        violations.append("document context exceeds the 30,000 character policy limit")

    return {
        "allowed": not violations,
        "violations": violations,
        "allowed_tools": [
            "get_stock_price",
            "get_financial_indicator",
            "get_stock_history",
            "get_stock_news",
            "retrieve_stock_news",
        ],
        "side_effects": "read_only",
        "max_replan_attempts": 1,
    }


def evaluate_output_gate(state: dict[str, Any]) -> dict[str, Any]:
    """Apply publication policy after generation, without modifying the draft."""
    reports = [
        state.get("fundamental_report") or "",
        state.get("technical_report") or "",
        state.get("sentiment_report") or "",
    ]
    active_reports = [r for r in reports if not r.startswith("[SKIPPED]")]
    successful_reports = [r for r in active_reports if r.startswith("[ANALYSIS_OK]")]
    draft = (state.get("final_decision") or "").strip()
    reasons: list[str] = []

    if not draft:
        reasons.append("missing final draft")
    if not successful_reports:
        reasons.append("no verified analyst report is available")
    if any(marker.lower() in draft.lower() for marker in _UNSUPPORTED_CLAIMS):
        reasons.append("draft contains an unsupported certainty or return claim")
    if "[TOOL_ERROR]" in draft or "[ANALYSIS_ABORT]" in draft:
        reasons.append("draft contains an upstream failure marker")

    if reasons:
        return {
            "publish_status": "blocked",
            "publish_reasons": reasons,
            "human_review_required": False,
            "draft_decision": draft,
            "final_decision": "[PUBLISH_BLOCKED] " + "; ".join(reasons),
        }

    # Investment recommendations are always high-impact external advice. They
    # remain a draft until a named reviewer explicitly approves publication.
    return {
        "publish_status": "requires_human_review",
        "publish_reasons": [
            "investment recommendation requires human approval before publication"
        ],
        "human_review_required": True,
        "draft_decision": draft,
        "final_decision": draft,
    }
