"""Aggregate reproducible Agent-ablation artifacts.

Each JSONL row represents one completed run against a frozen fixture. The
runner intentionally refuses rows without task, document and tool snapshot
hashes, so a live market response cannot be mistaken for a fair A/B result.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


REQUIRED_FIXTURE_KEYS = ("task_sha256", "document_snapshot_sha256", "tool_snapshot_sha256")


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def _cost(calls: list[dict[str, Any]], prices: dict[str, dict[str, float]]) -> float | None:
    total = 0.0
    known = True
    for call in calls:
        rate = prices.get(str(call.get("model")))
        if rate is None:
            known = False
            continue
        input_tokens = int(call.get("input_tokens") or 0)
        cache_hit = int(call.get("prompt_cache_hit_tokens") or 0)
        cache_miss = call.get("prompt_cache_miss_tokens")
        uncached_input = int(cache_miss) if cache_miss is not None else max(0, input_tokens - cache_hit)
        total += uncached_input * float(rate.get("input", 0))
        total += cache_hit * float(rate.get("cached_input", rate.get("input", 0)))
        total += int(call.get("output_tokens") or 0) * float(rate.get("output", 0))
    return round(total, 8) if known else None


def validate_row(row: dict[str, Any]) -> None:
    fixture = row.get("fixture") or {}
    missing = [key for key in REQUIRED_FIXTURE_KEYS if not fixture.get(key)]
    if missing:
        raise ValueError(f"fixture {row.get('fixture_id', '<unknown>')} missing {', '.join(missing)}")
    if not row.get("variant"):
        raise ValueError("ablation row requires variant")
    if not isinstance(row.get("run_metrics"), dict):
        raise ValueError("ablation row requires run_metrics from InvestmentRuntime")


def build_report(rows: list[dict[str, Any]], prices: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    prices = prices or {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fixtures: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for row in rows:
        validate_row(row)
        grouped[str(row["variant"])].append(row)
        fixture = row["fixture"]
        fixtures[str(row["variant"])].add(tuple(str(fixture[key]) for key in REQUIRED_FIXTURE_KEYS))

    variants: dict[str, Any] = {}
    for variant, samples in grouped.items():
        latencies = [float(item["run_metrics"].get("elapsed_ms") or 0) for item in samples]
        costs = [_cost((item.get("run_telemetry") or {}).get("llm_calls") or [], prices) for item in samples]
        known_costs = [cost for cost in costs if cost is not None]
        gate_passes = sum(item.get("publish_status") == "requires_human_review" for item in samples)
        variants[variant] = {
            "runs": len(samples),
            "fixture_snapshot_count": len(fixtures[variant]),
            "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95)},
            "mean_input_tokens": round(sum(float(item["run_metrics"].get("input_tokens") or 0) for item in samples) / len(samples), 3),
            "mean_tool_calls": round(sum(float(item["run_metrics"].get("tool_call_count") or 0) for item in samples) / len(samples), 3),
            "quality_gate_pass_rate": round(gate_passes / len(samples), 4),
            "cost": {
                "priced_runs": len(known_costs),
                "mean": round(sum(known_costs) / len(known_costs), 8) if known_costs else None,
                "p50": percentile(known_costs, 0.50),
                "p95": percentile(known_costs, 0.95),
            },
        }
    return {"rows": len(rows), "variants": variants}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate frozen-fixture Agent ablation runs")
    parser.add_argument("--runs", type=Path, required=True, help="JSONL run artifacts with fixture hashes")
    parser.add_argument("--prices", type=Path, help="JSON model price map, expressed per token")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in args.runs.read_text(encoding="utf-8").splitlines() if line.strip()]
    prices = json.loads(args.prices.read_text(encoding="utf-8")) if args.prices else {}
    report = build_report(rows, prices)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
