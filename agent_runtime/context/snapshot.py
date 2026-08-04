"""Deterministic, evidence-aware summaries for downstream workflow nodes."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any


_STATUS = re.compile(r"^\s*\[(ANALYSIS_OK|ANALYSIS_ABORT|TOOL_ERROR|SKIPPED)\]", re.M)
_KDJ = re.compile(r"\b([KDJ])\s*[=:：]\s*(-?\d+(?:\.\d+)?)", re.I)
_LEVELS = {
    "support": re.compile(r"(?:支持位|support)\s*[：:]\s*([^\n]+)", re.I),
    "resistance": re.compile(r"(?:压力位|resistance)\s*[：:]\s*([^\n]+)", re.I),
}


def _status(report: str) -> str:
    match = _STATUS.search(report or "")
    return match.group(1) if match else "UNKNOWN"


def _report_excerpt(report: str, limit: int = 420) -> str:
    """Keep source wording; remove only headings/protocol markers and whitespace."""
    lines: list[str] = []
    total = 0
    for raw_line in (report or "").splitlines():
        line = raw_line.strip().lstrip("-•")
        if not line or line.startswith("[") or line.startswith("#"):
            continue
        if total + len(line) > limit:
            remaining = max(0, limit - total)
            if remaining:
                lines.append(line[:remaining] + "…")
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines) or "No usable report content."


def _key_values(report: str) -> dict[str, str]:
    values = {name.upper(): value for name, value in _KDJ.findall(report or "")}
    for name, pattern in _LEVELS.items():
        match = pattern.search(report or "")
        if match:
            values[name] = match.group(1).strip()[:80]
    return values


def _report_ref(kind: str, report: str) -> str:
    digest = hashlib.sha256((report or "").encode("utf-8")).hexdigest()[:12]
    return f"state:{kind}:{digest}"


def build_context_snapshot(
    stock_code: str,
    reports: dict[str, str | None],
    *,
    document_citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a bounded snapshot without inventing claims or evidence."""
    citation_ids = [
        citation["evidence_id"]
        for citation in (document_citations or [])
        if citation.get("evidence_id")
    ]
    analysts: dict[str, Any] = {}
    unresolved_risks: list[str] = []
    tool_errors: list[str] = []

    for kind in ("technical", "fundamental", "sentiment"):
        report = reports.get(kind) or ""
        status = _status(report)
        analysts[kind] = {
            "status": status,
            "excerpt": _report_excerpt(report),
            "key_values": _key_values(report),
            "source_ref": _report_ref(kind, report),
            "evidence_ids": citation_ids if kind == "fundamental" else [],
        }
        if status in {"ANALYSIS_ABORT", "TOOL_ERROR", "UNKNOWN"}:
            tool_errors.append(f"{kind}:{status}")
        if status == "SKIPPED":
            unresolved_risks.append(f"{kind} analysis was not requested")

    if not citation_ids and reports.get("fundamental"):
        unresolved_risks.append("No document evidence IDs were supplied to the workflow")

    return {
        "schema_version": "context-snapshot/v1",
        "stock_code": stock_code,
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "analysts": analysts,
        "document_citations": document_citations or [],
        "unresolved_risks": unresolved_risks,
        "tool_errors": tool_errors,
    }
