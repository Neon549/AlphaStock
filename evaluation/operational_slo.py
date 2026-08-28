"""Aggregate explicit Agent telemetry into an operational SLO report.

This module consumes recorded runs only.  It does not generate load or call a
provider.  Operational counters must be explicitly present on every row; an
unknown provider/tool failure is not silently counted as success.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


REQUIRED_BOOLEAN_METRICS = (
    "provider_failed",
    "tool_failed",
    "retry_attempted",
    "retry_succeeded",
    "fallback_used",
    "cost_estimation_complete",
)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _validate_rows(rows: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        variant = str(row.get("variant", "default"))
        run_id = str(row.get("run_id", ""))
        key = (variant, run_id)
        if not run_id or key in seen:
            errors.append(f"{variant}/{run_id or '<unknown>'}: missing or duplicate run_id")
        seen.add(key)
        metrics = row.get("run_metrics")
        if not isinstance(metrics, dict):
            errors.append(f"{variant}/{run_id}: run_metrics is required")
            continue
        if metrics.get("schema_version") != "run_metrics/v2":
            errors.append(f"{variant}/{run_id}: run_metrics.schema_version must be run_metrics/v2")
        for field in ("elapsed_ms", "concurrency", "input_tokens", "output_tokens", "cost_usd"):
            if not _number(metrics.get(field)) or float(metrics[field]) < 0:
                errors.append(f"{variant}/{run_id}: run_metrics.{field} must be non-negative number")
        for field in REQUIRED_BOOLEAN_METRICS:
            if not isinstance(metrics.get(field), bool):
                errors.append(f"{variant}/{run_id}: run_metrics.{field} must be boolean")
        if metrics.get("retry_succeeded") and not metrics.get("retry_attempted"):
            errors.append(f"{variant}/{run_id}: retry_succeeded cannot be true without retry_attempted")
        if metrics.get("cost_estimation_complete") is False:
            errors.append(f"{variant}/{run_id}: cost estimation is incomplete")
    return errors


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [row["run_metrics"] for row in rows]
    latency = [float(item["elapsed_ms"]) for item in metrics]
    concurrency = [float(item["concurrency"]) for item in metrics]
    retry_attempted = sum(bool(item["retry_attempted"]) for item in metrics)
    retry_succeeded = sum(bool(item["retry_succeeded"]) for item in metrics)
    return {
        "runs": len(rows),
        "concurrency": {"mean": round(sum(concurrency) / len(concurrency), 3), "max": max(concurrency)},
        "latency_ms": {
            "p50": _percentile(latency, 0.50), "p95": _percentile(latency, 0.95), "p99": _percentile(latency, 0.99),
        },
        "provider_failure_rate": round(sum(bool(item["provider_failed"]) for item in metrics) / len(metrics), 4),
        "tool_failure_rate": round(sum(bool(item["tool_failed"]) for item in metrics) / len(metrics), 4),
        "retry_attempt_rate": round(retry_attempted / len(metrics), 4),
        "retry_success_rate": round(retry_succeeded / retry_attempted, 4) if retry_attempted else None,
        "fallback_rate": round(sum(bool(item["fallback_used"]) for item in metrics) / len(metrics), 4),
        "tokens": {
            "mean_input": round(sum(float(item["input_tokens"]) for item in metrics) / len(metrics), 3),
            "mean_output": round(sum(float(item["output_tokens"]) for item in metrics) / len(metrics), 3),
            "mean_total": round(sum(float(item["input_tokens"]) + float(item["output_tokens"]) for item in metrics) / len(metrics), 3),
        },
        "cost": {"mean_usd": round(sum(float(item["cost_usd"]) for item in metrics) / len(metrics), 8)},
    }


def build_operational_slo_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    errors = ["telemetry dataset must not be empty"] if not rows else _validate_rows(rows)
    if errors:
        return {
            "schema_version": "alpha-stock-operational-slo/v1", "valid": False, "errors": errors,
            "claim_boundary": "这是已记录 telemetry 的聚合，不是压测结果，也不代表线上全量流量。",
        }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("variant", "default"))].append(row)
    return {
        "schema_version": "alpha-stock-operational-slo/v1",
        "valid": True,
        "total_runs": len(rows),
        "variants": {variant: _aggregate(items) for variant, items in sorted(grouped.items())},
        "claim_boundary": "这是已记录 telemetry 的聚合，不是主动压测结果，也不代表线上全量流量；缺失指标不会被当作零失败。",
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("telemetry JSONL rows must be objects")
            rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate explicit AlphaStock Agent SLO telemetry")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = build_operational_slo_report(_load_jsonl(args.runs))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
