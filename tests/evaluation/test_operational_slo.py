from evaluation.operational_slo import build_operational_slo_report


def _run(run_id, elapsed=100, *, provider_failed=False, tool_failed=False, retry_attempted=False, retry_succeeded=False, fallback_used=False):
    return {
        "variant": "bge-default",
        "run_id": run_id,
        "run_metrics": {
            "schema_version": "run_metrics/v2",
            "elapsed_ms": elapsed,
            "concurrency": 2,
            "input_tokens": 1000,
            "output_tokens": 200,
            "cost_usd": 0.01,
            "provider_failed": provider_failed,
            "tool_failed": tool_failed,
            "retry_attempted": retry_attempted,
            "retry_succeeded": retry_succeeded,
            "fallback_used": fallback_used,
            "cost_estimation_complete": True,
        },
    }


def test_operational_slo_aggregates_latency_failures_recovery_and_cost():
    report = build_operational_slo_report([
        _run("1", elapsed=100),
        _run("2", elapsed=300, provider_failed=True, retry_attempted=True, retry_succeeded=True, fallback_used=True),
        _run("3", elapsed=200, tool_failed=True, retry_attempted=True),
    ])
    variant = report["variants"]["bge-default"]
    assert report["valid"] is True
    assert variant["latency_ms"]["p95"] == 290.0
    assert variant["latency_ms"]["p99"] == 298.0
    assert variant["provider_failure_rate"] == 0.3333
    assert variant["tool_failure_rate"] == 0.3333
    assert variant["retry_success_rate"] == 0.5
    assert variant["fallback_rate"] == 0.3333
    assert variant["tokens"]["mean_total"] == 1200.0


def test_operational_slo_fails_closed_when_metric_is_missing():
    row = _run("1")
    del row["run_metrics"]["provider_failed"]
    report = build_operational_slo_report([row])
    assert report["valid"] is False
    assert any("provider_failed" in error for error in report["errors"])


def test_operational_slo_rejects_successful_retry_without_attempt():
    report = build_operational_slo_report([_run("1", retry_succeeded=True)])
    assert report["valid"] is False
    assert any("retry_succeeded" in error for error in report["errors"])


def test_operational_slo_rejects_empty_input():
    report = build_operational_slo_report([])
    assert report["valid"] is False
