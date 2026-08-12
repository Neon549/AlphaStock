import unittest
from control_plane.observability import redact_query, record_rag_event, run_telemetry_scope


class RunObservabilityTests(unittest.TestCase):
    def test_query_redaction_and_safe_summary(self):
        self.assertEqual(redact_query("email a@b.com 13800138000")["query_preview"], "email [email] [phone]")
        with run_telemetry_scope("run-1", query="hello", metadata={}) as telemetry:
            record_rag_event("retrieval", {"status": "ok", "retrieved_chunk_count": 2, "top_k": [{"id": "x"}]})
            record_rag_event("citation_validation", {"status": "passed", "citation_count": 1})
        summary = telemetry.summary()
        self.assertEqual(summary["retrieved_chunk_count"], 2)
        self.assertEqual(summary["citation_validation_status"], "passed")


if __name__ == "__main__":
    unittest.main()
