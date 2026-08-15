"""Fail-closed release quality gate for AlphaStock Agent changes.

The existing regression runner protects deterministic code and governance
contracts.  This module adds the missing business-quality gate around an
already-produced, immutable JSON report.  It intentionally does not invoke a
model, retrieve live data, or infer missing metrics: a missing check blocks a
release rather than silently becoming ``N/A``.

Expected report shape (see ``openspec/release-quality-gate-v1/spec.md``)::

    {
      "schema_version": "alpha-release-quality-gate-input/v1",
      "candidate_version": "...",
      "checks": {
        "code_regression": {"passed": true},
        "governance_regression": {"passed": true},
        "rag": {"metrics": {"recall_at_10": {"candidate": 0.7, "baseline": 0.6}}},
        "e2e": {"metrics": {"success_rate": {"candidate": 0.8, "baseline": 0.8}}},
        "citation": {"metrics": {"citation_accuracy": {"candidate": 0.9, "baseline": 0.9}}},
        "latency": {"p95_ms": 1800, "max_p95_ms": 2500},
        "cost": {"mean_cost_usd": 0.02, "max_mean_cost_usd": 0.05,
                  "mean_tokens": 1600, "max_mean_tokens": 2500},
        "red_team": {"total_cases": 40, "high_risk_failures": 0}
      }
    }

``candidate`` metrics must not decline from ``baseline``.  This is a
non-regression gate, not a claim that any baseline is production quality.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "alpha-release-quality-gate/v1"
REQUIRED_CHECKS = (
    "code_regression",
    "governance_regression",
    "rag",
    "e2e",
    "citation",
    "latency",
    "cost",
    "red_team",
)
NON_REGRESSION_CHECKS = ("rag", "e2e", "citation")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _result(check: str, passed: bool, reasons: list[str], **details: Any) -> dict[str, Any]:
    return {"check": check, "passed": bool(passed), "reasons": reasons, **details}


def _check_boolean(checks: dict[str, Any], name: str) -> dict[str, Any]:
    payload = checks.get(name)
    if not isinstance(payload, dict) or not isinstance(payload.get("passed"), bool):
        return _result(name, False, [f"{name}.passed must be boolean"])
    return _result(name, payload["passed"], [] if payload["passed"] else [f"{name} regression failed"])


def _check_non_regression(checks: dict[str, Any], name: str) -> dict[str, Any]:
    payload = checks.get(name)
    if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict) or not payload["metrics"]:
        return _result(name, False, [f"{name}.metrics must be a non-empty object"])
    reasons: list[str] = []
    metrics: dict[str, Any] = {}
    for metric_name, raw in payload["metrics"].items():
        if not isinstance(raw, dict) or not _is_number(raw.get("candidate")) or not _is_number(raw.get("baseline")):
            reasons.append(f"{name}.{metric_name} requires numeric candidate and baseline")
            continue
        candidate = float(raw["candidate"])
        baseline = float(raw["baseline"])
        if not 0.0 <= candidate <= 1.0 or not 0.0 <= baseline <= 1.0:
            reasons.append(f"{name}.{metric_name} candidate and baseline must be in [0, 1]")
            continue
        min_delta = float(raw.get("min_delta", 0.0)) if _is_number(raw.get("min_delta", 0.0)) else None
        if min_delta is None:
            reasons.append(f"{name}.{metric_name}.min_delta must be numeric")
            continue
        delta = candidate - baseline
        if delta < min_delta:
            reasons.append(
                f"{name}.{metric_name} declined: candidate={candidate:g}, baseline={baseline:g}, min_delta={min_delta:g}"
            )
        metrics[str(metric_name)] = {
            "candidate": candidate,
            "baseline": baseline,
            "delta": round(delta, 6),
            "min_delta": min_delta,
        }
    return _result(name, not reasons and bool(metrics), reasons, metrics=metrics)


def _check_latency(checks: dict[str, Any]) -> dict[str, Any]:
    payload = checks.get("latency")
    if not isinstance(payload, dict) or not _is_number(payload.get("p95_ms")) or not _is_number(payload.get("max_p95_ms")):
        return _result("latency", False, ["latency requires numeric p95_ms and max_p95_ms"])
    p95, maximum = float(payload["p95_ms"]), float(payload["max_p95_ms"])
    if p95 < 0 or maximum < 0:
        return _result("latency", False, ["latency values must be non-negative"], p95_ms=p95, max_p95_ms=maximum)
    passed = p95 <= maximum
    return _result("latency", passed, [] if passed else [f"p95 latency {p95:g}ms exceeds {maximum:g}ms"], p95_ms=p95, max_p95_ms=maximum)


def _check_cost(checks: dict[str, Any]) -> dict[str, Any]:
    payload = checks.get("cost")
    required = ("mean_cost_usd", "max_mean_cost_usd", "mean_tokens", "max_mean_tokens")
    if not isinstance(payload, dict) or any(not _is_number(payload.get(key)) for key in required):
        return _result("cost", False, [f"cost requires numeric {', '.join(required)}"])
    cost, max_cost = float(payload["mean_cost_usd"]), float(payload["max_mean_cost_usd"])
    tokens, max_tokens = float(payload["mean_tokens"]), float(payload["max_mean_tokens"])
    reasons: list[str] = []
    if min(cost, max_cost, tokens, max_tokens) < 0:
        reasons.append("cost and token values must be non-negative")
    if cost > max_cost:
        reasons.append(f"mean cost {cost:g} exceeds {max_cost:g}")
    if tokens > max_tokens:
        reasons.append(f"mean tokens {tokens:g} exceeds {max_tokens:g}")
    return _result(
        "cost", not reasons, reasons,
        mean_cost_usd=cost, max_mean_cost_usd=max_cost, mean_tokens=tokens, max_mean_tokens=max_tokens,
    )


def _check_red_team(checks: dict[str, Any]) -> dict[str, Any]:
    payload = checks.get("red_team")
    if not isinstance(payload, dict) or not _is_number(payload.get("total_cases")) or not _is_number(payload.get("high_risk_failures")):
        return _result("red_team", False, ["red_team requires numeric total_cases and high_risk_failures"])
    total, failures = int(payload["total_cases"]), int(payload["high_risk_failures"])
    reasons: list[str] = []
    if total <= 0:
        reasons.append("red_team.total_cases must be greater than zero")
    if failures < 0 or failures > total:
        reasons.append("red_team.high_risk_failures must be between zero and total_cases")
    if failures != 0:
        reasons.append(f"red team found {failures} high-risk failure(s)")
    return _result("red_team", not reasons, reasons, total_cases=total, high_risk_failures=failures)


def evaluate_release_quality_gate(report: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a release report and return an auditable, fail-closed result."""

    if not isinstance(report, dict):
        raise ValueError("release report must be an object")
    checks = report.get("checks")
    errors: list[str] = []
    if not isinstance(checks, dict):
        checks = {}
        errors.append("checks must be an object")
    missing = [name for name in REQUIRED_CHECKS if name not in checks]
    errors.extend(f"missing required check: {name}" for name in missing)

    results: dict[str, dict[str, Any]] = {}
    for name in ("code_regression", "governance_regression"):
        results[name] = _check_boolean(checks, name)
    for name in NON_REGRESSION_CHECKS:
        results[name] = _check_non_regression(checks, name)
    results["latency"] = _check_latency(checks)
    results["cost"] = _check_cost(checks)
    results["red_team"] = _check_red_team(checks)

    failed_checks = [name for name, result in results.items() if not result["passed"]]
    errors.extend(reason for name in failed_checks for reason in results[name].get("reasons", []))
    allowed = not errors and not failed_checks
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_version": report.get("candidate_version"),
        "baseline_version": report.get("baseline_version"),
        "release_allowed": allowed,
        "failed_checks": failed_checks,
        "errors": errors,
        "checks": results,
        "claim_boundary": "release_allowed 只表示该候选版本通过声明的质量门禁；不等于生产数据集已准入，也不等于线上质量或安全的普遍保证。",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the fail-closed AlphaStock release quality gate")
    parser.add_argument("--report", type=Path, required=True, help="JSON report containing candidate checks")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate_release_quality_gate(json.loads(args.report.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["release_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
