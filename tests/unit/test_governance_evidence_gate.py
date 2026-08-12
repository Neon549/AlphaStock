import unittest

from agent_runtime.workflows.governance import evaluate_output_gate


def _state(evidence):
    return {
        "fundamental_report": "[ANALYSIS_OK] verified report",
        "technical_report": "[SKIPPED] not requested",
        "sentiment_report": "[SKIPPED] not requested",
        "final_decision": "decision draft",
        "research_evidence": evidence,
    }


class EvidenceGateTests(unittest.TestCase):
    def test_blocks_stale_market_evidence(self):
        result = evaluate_output_gate(_state([{
            "ok": True,
            "source_kind": "market_evidence",
            "freshness": {"status": "stale"},
        }]))
        self.assertEqual(result["publish_status"], "blocked")
        self.assertFalse(result["evidence_gate"]["passed"])

    def test_accepts_timestamped_market_evidence_but_keeps_human_review(self):
        result = evaluate_output_gate(_state([{
            "ok": True,
            "source_kind": "market_evidence",
            "result_ref": "tool-result:market-price:fixture",
            "freshness": {"status": "retrieved"},
        }]))
        self.assertEqual(result["publish_status"], "requires_human_review")
        self.assertTrue(result["evidence_gate"]["passed"])

    def test_degraded_cache_is_never_current_market_evidence(self):
        result = evaluate_output_gate(_state([{
            "ok": True,
            "source_kind": "degraded_cache",
            "freshness": {"status": "cached", "cache_age_seconds": 5},
        }]))
        self.assertEqual(result["publish_status"], "blocked")
        self.assertFalse(result["evidence_gate"]["passed"])

    def test_accepts_page_cited_document_evidence(self):
        result = evaluate_output_gate(_state([{
            "ok": True,
            "source_kind": "document_evidence",
            "citations": [{"evidence_id": "doc:page-8", "page": 8}],
        }]))
        self.assertEqual(result["publish_status"], "requires_human_review")


if __name__ == "__main__":
    unittest.main()
