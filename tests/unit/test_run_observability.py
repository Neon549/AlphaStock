import unittest
from unittest.mock import patch

from control_plane.observability import (
    current_run_id,
    record_llm_call,
    register_tool_artifact,
    record_rag_event,
    redact_query,
    run_telemetry_scope,
)


class RunObservabilityTests(unittest.TestCase):
    def test_scope_aggregates_model_and_tool_metrics(self):
        with run_telemetry_scope("run-fixture") as telemetry:
            self.assertEqual(current_run_id(), "run-fixture")
            record_llm_call(
                model="deepseek-chat", latency_ms=12.4, success=True, used_backup=False,
                usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                       "prompt_cache_hit_tokens": 50},
                recovery={"provider_role": "primary", "recovery_action": "primary_success"},
            )
            record_llm_call(
                model="qwen", latency_ms=4.0, success=True, used_backup=True,
                usage=None,
                recovery={
                    "provider_role": "backup", "recovery_action": "backup_success",
                    "degradation_mode": "draft_only",
                },
            )
            register_tool_artifact("tool-result:price:abc", {
                "tool": "market-price", "source_kind": "market_evidence",
                "citations": [], "content": "fixture", "content_sha256": "abc",
            })
        summary = telemetry.summary([{"event": "skill_result", "latency_ms": 8.0}])
        self.assertEqual(summary["run_id"], "run-fixture")
        self.assertEqual(summary["input_tokens"], 100)
        self.assertEqual(summary["prompt_cache_hit_tokens"], 50)
        self.assertEqual(summary["llm_backup_call_count"], 1)
        self.assertEqual(summary["llm_draft_only_call_count"], 1)
        self.assertEqual(summary["tool_call_count"], 1)
        self.assertEqual(telemetry.export()["tool_artifacts"]["tool-result:price:abc"]["content"], "fixture")
        self.assertIsNone(current_run_id())

    def test_rag_summary_is_safe_and_root_trace_receives_redacted_query(self):
        with (
            patch("control_plane.observability._start_langfuse_run") as start_trace,
            patch("control_plane.observability._finish_langfuse_run") as finish_trace,
            patch("control_plane.observability._trace_langfuse_rag_event") as rag_span,
        ):
            with run_telemetry_scope("run-rag", query="联系 13800138000 或 foo@example.com") as telemetry:
                record_rag_event("retrieval", {
                    "status": "ok", "retrieved_chunk_count": 2,
                    "top_k": [{"evidence_id": "chunk-1", "distance": 0.12}],
                    "rerank": {"applied": False, "reason": "not_configured"},
                })
                record_rag_event("citation_validation", {
                    "status": "passed", "citation_count": 2,
                })
            metrics = telemetry.summary()
            public = telemetry.public_summary(metrics)

        start_trace.assert_called_once()
        self.assertEqual(start_trace.call_args.kwargs["query"], "联系 13800138000 或 foo@example.com")
        redacted = redact_query("联系 13800138000 或 foo@example.com")
        self.assertEqual(redacted["query_preview"], "联系 [phone] 或 [email]")
        self.assertNotIn("foo@example.com", str(redacted))
        self.assertEqual(public["retrieval_count"], 1)
        self.assertEqual(public["retrieved_chunk_count"], 2)
        self.assertEqual(public["citation_validation_status"], "passed")
        self.assertNotIn("top_k", public)
        self.assertEqual(rag_span.call_count, 2)
        finish_trace.assert_called_once()

    def test_redact_query_does_not_return_the_original_hashable_text(self):
        value = redact_query("身份证 11010519491231002X")
        self.assertEqual(value["query_preview"], "身份证 [id]")
        self.assertEqual(len(value["query_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
