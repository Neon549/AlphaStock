"""Structured snapshots for current market and financial evidence.

The existing tools intentionally return human-readable text for compatibility
with the Agent prompts and API clients.  This module derives a small,
deterministic structured record from those results without making the tool
layer depend on PostgreSQL availability.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any


_TOOL_TYPES = {
    "market-price": "quote",
    "financial-indicators": "financial_indicator",
    "market-history": "daily_history",
    "get_stock_history": "daily_history",
}
_LABELS = {
    "股票名称": "stock_name",
    "最新价": "price",
    "涨跌幅": "change_pct",
    "成交量": "volume",
    "总市值": "market_cap",
    "行业": "industry",
    "报告期": "report_period",
    "report_period": "report_period",
    "report_period_raw": "report_period_raw",
    "report_period_source_field": "report_period_source_field",
    "report_type": "report_type",
    "price": "price",
    "change_pct": "change_pct",
    "volume": "volume",
    "market_cap": "market_cap",
    "source": "data_source",
    "营业总收入": "revenue",
    "营业收入": "revenue",
    "净利润": "net_profit",
    "ROE": "roe",
    "毛利率": "gross_margin",
    "期间最高价": "period_high",
    "期间最低价": "period_low",
    "最新收盘价": "latest_close",
    "retrieved_at": "retrieved_at",
    "data_source": "data_source",
    "数据来源": "data_source",
}
_DATE = re.compile(r"((?:19|20)\d{2})[-/]?(\d{2})[-/]?(\d{2})")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:")
_MISSING = {"", "n/a", "na", "none", "null", "nan", "未提供", "未知"}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def extract_market_fields(content: str) -> dict[str, str]:
    """Extract known ``key=value`` and Chinese ``label：value`` fields."""

    fields: dict[str, str] = {}
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            normalized_key = _LABELS.get(key.strip())
            if normalized_key:
                fields[normalized_key] = _clean(value)
        if "：" in line or ":" in line:
            separator = "：" if "：" in line else ":"
            key, value = line.split(separator, 1)
            normalized_key = _LABELS.get(key.strip())
            if normalized_key:
                fields[normalized_key] = _clean(value)
    return fields


def _scalar(value: str) -> object:
    """Keep non-numeric finance values intact, while typing simple numbers."""

    text = _clean(value)
    if text.lower() in _MISSING:
        return None
    compact = text.replace(",", "").replace("，", "")
    number_text = compact[:-1] if compact.endswith("%") else compact
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", number_text):
        number = float(number_text)
        return int(number) if number.is_integer() else number
    return text


def _date_value(value: str | None) -> str | None:
    match = _DATE.search(value or "")
    if not match:
        return None
    year, month, day = map(int, match.groups())
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _datetime_value(value: str | None, fallback: datetime) -> datetime:
    text = _clean(value)
    if text and _ISO_DATETIME.match(text):
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
    return fallback


def _history_rows(content: str) -> list[dict[str, object]]:
    """Parse the stable DataFrame text emitted by the history tool."""

    rows: list[dict[str, object]] = []
    for raw_line in str(content or "").splitlines():
        parts = raw_line.strip().split()
        if len(parts) < 6 or not _DATE.fullmatch(parts[0].replace("-", "")):
            continue
        values = parts[1:7]
        if len(values) < 5:
            continue
        rows.append({
            "date": _date_value(parts[0]),
            "open": _scalar(values[0]),
            "close": _scalar(values[1]),
            "high": _scalar(values[2]),
            "low": _scalar(values[3]),
            "volume": _scalar(values[4]),
            "change_pct": _scalar(values[5]) if len(values) > 5 else None,
        })
    return rows


def build_market_evidence_record(
    tool: str,
    stock_code: str,
    content: str,
    *,
    result_ref: str | None = None,
    captured_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Build an append-only structured record from a successful market tool result.

    Returns ``None`` for unsupported tools, invalid stock codes, or explicit
    tool errors.  The record is safe to pass to a PostgreSQL JSONB insert.
    """

    evidence_type = _TOOL_TYPES.get(str(tool))
    code = _clean(stock_code)
    raw_content = str(content or "")
    if not evidence_type or not re.fullmatch(r"[036]\d{5}", code) or raw_content.startswith("[TOOL_ERROR]"):
        return None

    captured_at = captured_at or datetime.now().astimezone()
    fields = extract_market_fields(raw_content)
    retrieved_at = _datetime_value(fields.get("retrieved_at"), captured_at)
    history = _history_rows(raw_content) if evidence_type == "daily_history" else []
    period_end = _date_value(fields.get("report_period"))
    if evidence_type == "daily_history" and history:
        period_end = str(history[-1].get("date") or "") or period_end
    payload = {key: _scalar(value) for key, value in fields.items()}
    payload["stock_code"] = code
    if evidence_type == "daily_history":
        payload["history"] = history
        payload["history_row_count"] = len(history)
    payload["content_sha256"] = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

    if evidence_type == "financial_indicator" and not period_end:
        quality_status = "missing_report_period"
    elif evidence_type == "quote" and payload.get("price") is None:
        quality_status = "missing_price"
    elif evidence_type == "daily_history" and not history:
        quality_status = "missing_history_rows"
    else:
        quality_status = "valid"

    content_hash = str(payload["content_sha256"])
    return {
        "evidence_id": f"market:{evidence_type}:{code}:{content_hash[:24]}",
        "stock_code": code,
        "stock_name": fields.get("stock_name"),
        "evidence_type": evidence_type,
        "as_of_at": retrieved_at.isoformat(),
        "period_end": period_end,
        "retrieved_at": retrieved_at.isoformat(),
        "source": fields.get("data_source") or "unknown",
        "source_url": fields.get("source_url"),
        "payload": payload,
        "content_sha256": content_hash,
        "result_ref": result_ref,
        "quality_status": quality_status,
    }


def get_latest_market_evidence(
    stock_code: str,
    *,
    evidence_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Read recent structured evidence; raw tool text remains in its artifact store."""

    if not re.fullmatch(r"[036]\d{5}", _clean(stock_code)):
        raise ValueError("stock_code must be a six-digit A-share code")
    if limit <= 0 or limit > 200:
        raise ValueError("limit must be between 1 and 200")
    from db import get_conn

    clauses = ["stock_code = %s"]
    params: list[Any] = [_clean(stock_code)]
    if evidence_type:
        clauses.append("evidence_type = %s")
        params.append(evidence_type)
    params.append(limit)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT evidence_id, stock_code, stock_name, evidence_type,
                       as_of_at, period_end, retrieved_at, source, source_url,
                       payload, content_sha256, result_ref, quality_status
                FROM market_evidence
                WHERE {' AND '.join(clauses)}
                ORDER BY COALESCE(as_of_at, retrieved_at) DESC, retrieved_at DESC
                LIMIT %s
                """,
                params,
            )
            columns = [item.name for item in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def persist_market_evidence_record(record: dict[str, Any]) -> str | None:
    """Best-effort direct insert for non-Agent API calls.

    Agent runs normally persist through ``PostgresRunStore`` so the evidence
    and run artifact share a transaction.  This helper is for direct read-only
    endpoints such as ``/stocks/info`` where no Agent run exists.
    """

    required = ("evidence_id", "stock_code", "evidence_type", "retrieved_at", "content_sha256")
    if any(not record.get(key) for key in required):
        return None
    try:
        from db import get_conn

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO market_evidence
                        (evidence_id, stock_code, stock_name, evidence_type,
                         as_of_at, period_end, retrieved_at, source, source_url,
                         payload, content_sha256, result_ref, quality_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (evidence_id) DO NOTHING
                    """,
                    (
                        record["evidence_id"], record["stock_code"], record.get("stock_name"),
                        record["evidence_type"], record.get("as_of_at"), record.get("period_end"),
                        record["retrieved_at"], record.get("source") or "unknown", record.get("source_url"),
                        json_payload(record), record["content_sha256"], record.get("result_ref"),
                        record.get("quality_status") or "valid",
                    ),
                )
            conn.commit()
        return str(record["evidence_id"])
    except Exception:
        return None


def json_payload(record: dict[str, Any]) -> str:
    """Serialize a record payload for psycopg2 without importing DB code here."""

    return json.dumps(record.get("payload") or {}, ensure_ascii=False, separators=(",", ":"))
