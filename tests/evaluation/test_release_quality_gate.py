from evaluation.release_quality_gate import evaluate_release_quality_gate


def _report():
    return {
        "candidate_version": "candidate-2026-08-15",
        "baseline_version": "baseline-2026-08-14",
        "checks": {
            "code_regression": {"passed": True},
            "governance_regression": {"passed": True},
            "rag": {"metrics": {"recall_at_10": {"candidate": 0.71, "baseline": 0.70}}},
            "e2e": {"metrics": {"success_rate": {"candidate": 0.82, "baseline": 0.82}}},
            "citation": {"metrics": {"citation_accuracy": {"candidate": 0.91, "baseline": 0.90}}},
            "latency": {"p95_ms": 1800, "max_p95_ms": 2500},
            "cost": {"mean_cost_usd": 0.02, "max_mean_cost_usd": 0.05, "mean_tokens": 1600, "max_mean_tokens": 2500},
            "red_team": {"total_cases": 40, "high_risk_failures": 0},
        },
    }


def test_quality_gate_passes_only_when_all_p0_checks_pass():
    result = evaluate_release_quality_gate(_report())
    assert result["release_allowed"] is True
    assert result["failed_checks"] == []
    assert result["checks"]["rag"]["metrics"]["recall_at_10"]["delta"] == 0.01


def test_quality_gate_blocks_metric_regression():
    report = _report()
    report["checks"]["rag"]["metrics"]["recall_at_10"]["candidate"] = 0.69
    result = evaluate_release_quality_gate(report)
    assert result["release_allowed"] is False
    assert "rag" in result["failed_checks"]
    assert any("declined" in error for error in result["errors"])


def test_quality_gate_blocks_latency_cost_and_red_team_failures():
    report = _report()
    report["checks"]["latency"]["p95_ms"] = 3000
    report["checks"]["cost"]["mean_tokens"] = 3000
    report["checks"]["red_team"]["high_risk_failures"] = 1
    result = evaluate_release_quality_gate(report)
    assert result["release_allowed"] is False
    assert {"latency", "cost", "red_team"}.issubset(result["failed_checks"])


def test_quality_gate_fails_closed_when_a_required_check_is_missing():
    report = _report()
    del report["checks"]["citation"]
    result = evaluate_release_quality_gate(report)
    assert result["release_allowed"] is False
    assert "citation" in result["failed_checks"]
    assert any("missing required check: citation" in error for error in result["errors"])


def test_quality_gate_rejects_empty_red_team_run():
    report = _report()
    report["checks"]["red_team"]["total_cases"] = 0
    result = evaluate_release_quality_gate(report)
    assert result["release_allowed"] is False
    assert "red_team" in result["failed_checks"]
