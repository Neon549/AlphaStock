import unittest

from evaluation.ablation_report import build_report


def _row(variant, latency):
    return {
        "fixture_id": "fixture-1",
        "fixture": {
            "task_sha256": "task", "document_snapshot_sha256": "document", "tool_snapshot_sha256": "tools",
        },
        "variant": variant,
        "publish_status": "requires_human_review",
        "run_metrics": {"elapsed_ms": latency, "input_tokens": 100, "tool_call_count": 2},
        "run_telemetry": {"llm_calls": [{
            "model": "model-a", "input_tokens": 100, "output_tokens": 10,
            "prompt_cache_hit_tokens": 20,
        }]},
    }


class AblationReportTests(unittest.TestCase):
    def test_reports_latency_cost_and_gate_rate_for_frozen_fixtures(self):
        report = build_report(
            [_row("python", 10), _row("python", 30)],
            {"model-a": {"input": 1, "cached_input": 0.5, "output": 2}},
        )
        variant = report["variants"]["python"]
        self.assertEqual(variant["runs"], 2)
        self.assertEqual(variant["latency_ms"]["p50"], 20.0)
        self.assertEqual(variant["quality_gate_pass_rate"], 1.0)
        self.assertEqual(variant["cost"]["priced_runs"], 2)
        self.assertEqual(variant["cost"]["mean"], 110.0)

    def test_rejects_live_or_incomplete_fixture(self):
        row = _row("python", 10)
        del row["fixture"]["tool_snapshot_sha256"]
        with self.assertRaises(ValueError):
            build_report([row])


if __name__ == "__main__":
    unittest.main()
