import unittest

from agent_runtime.context.budget import ContextBlock, pack_context
from agent_runtime.context.compaction import compact_tool_observations, is_context_overflow_error
from agent_runtime.context.snapshot import build_context_snapshot


class ContextSnapshotTests(unittest.TestCase):
    def test_snapshot_extracts_deterministic_fields_and_preserves_citations(self):
        snapshot = build_context_snapshot(
            "600519",
            {
                "technical": "[ANALYSIS_OK]\nK=21.4 D=26.8 J=10.6\n支持位：1500\n压力位：1600",
                "fundamental": "[ANALYSIS_OK]\n经营现金流改善",
                "sentiment": "[SKIPPED] not requested",
            },
            document_citations=[{"evidence_id": "chunk:p12:3", "page": 12}],
        )
        technical = snapshot["analysts"]["technical"]
        self.assertEqual(technical["key_values"]["K"], "21.4")
        self.assertEqual(technical["key_values"]["support"], "1500")
        self.assertEqual(snapshot["analysts"]["fundamental"]["evidence_ids"], ["chunk:p12:3"])
        self.assertIn("sentiment analysis was not requested", snapshot["unresolved_risks"])

    def test_budget_keeps_high_priority_blocks_without_cutting_them(self):
        result = pack_context(
            [ContextBlock("critical", "x" * 500, 100), ContextBlock("optional", "y" * 500, 1)],
            max_tokens=200,
        )
        self.assertIn("critical", result["text"])
        self.assertIn("optional", result["omitted_blocks"])
        self.assertEqual(result["mode"], "compacted")

    def test_tool_microcompact_keeps_provenance_before_source_preview(self):
        observations, changed = compact_tool_observations(
            [{
                "tool": "document-rag",
                "ok": True,
                "content": "x" * 1_000,
                "citations": [{"evidence_id": "chunk:p12:3"}],
                "freshness": {"status": "retrieved"},
            }],
            max_tokens=500,
            preview_chars=100,
        )
        self.assertTrue(changed)
        self.assertEqual(observations[0]["citations"][0]["evidence_id"], "chunk:p12:3")
        self.assertIn("content_ref", observations[0])
        self.assertIn("full result omitted", observations[0]["preview"])

    def test_context_overflow_detection_handles_413(self):
        self.assertTrue(is_context_overflow_error(RuntimeError("HTTP 413 prompt too long")))


if __name__ == "__main__":
    unittest.main()
