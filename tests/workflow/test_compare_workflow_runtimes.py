import unittest

from scripts.compare_workflow_runtimes import (
    compare_backtest_contracts,
    compare_contracts,
    summarize_backtest_result,
    summarize_result,
)


class RuntimeComparisonTests(unittest.TestCase):
    def test_summary_uses_stable_contract_fields(self):
        summary = summarize_result(
            {
                "publish_status": "requires_human_review",
                "human_review_required": True,
                "final_decision": "决策：买入\n建议仓位：10%",
                "technical_report": "[ANALYSIS_OK] signal",
                "fundamental_report": "[SKIPPED] not requested",
                "sentiment_report": "[SKIPPED] not requested",
                "research_evidence": [{"source": "market"}],
            }
        )
        self.assertEqual(summary["decision_category"], "买入")
        self.assertEqual(summary["analyst_status"]["technical"], "ok")
        self.assertEqual(summary["research_evidence_count"], 1)
        self.assertFalse(summary["risk_contract"]["has_risk_assessment"])

    def test_comparison_flags_only_contract_level_differences(self):
        base = {
            "publish_status": "requires_human_review",
            "human_review_required": True,
            "final_decision": "决策：持有观望",
            "technical_report": "[ANALYSIS_OK] signal",
        }
        self.assertTrue(compare_contracts(base, {**base, "technical_report": "[ANALYSIS_OK] rewritten"})["contract_match"])
        self.assertTrue(compare_contracts(base, {**base, "final_decision": "决策：买入"})["contract_match"])
        changed = compare_contracts(base, {**base, "publish_status": "blocked"})
        self.assertFalse(changed["contract_match"])
        self.assertIn("publish_status", changed["differences"])

    def test_backtest_comparison_includes_optimizer_skip_contract(self):
        failed = {
            "backtest_request": {"stock_code": "600519", "strategy": "kdj_macd"},
            "backtest_report": "[TOOL_ERROR] unavailable",
            "backtest_summary": "data unavailable",
            "backtest_optimizer_skipped": True,
        }
        summary = summarize_backtest_result(failed)
        self.assertEqual(summary["backtest_status"], "tool_error")
        self.assertTrue(summary["optimizer_skipped"])
        self.assertTrue(compare_backtest_contracts(failed, dict(failed))["contract_match"])
