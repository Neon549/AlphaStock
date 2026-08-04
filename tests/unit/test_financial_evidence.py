import unittest

import pandas as pd

from agent_runtime.agents.research_harness import _market_metadata
from agent_runtime.evidence.cards import build_evidence_cards
from tools.akshare_tools import _latest_financial_record


class FinancialEvidenceTests(unittest.TestCase):
    def test_latest_financial_record_uses_explicit_period_column_not_first_row(self):
        frame = pd.DataFrame([
            {"报告期": "1998", "净利润": "1.47亿"},
            {"报告期": "2024", "净利润": "747.34亿"},
            {"报告期": "2023", "净利润": "627.94亿"},
        ])
        record, source_field, raw_period, normalized_period = _latest_financial_record(frame)
        self.assertEqual(source_field, "报告期")
        self.assertEqual(raw_period, "2024")
        self.assertEqual(normalized_period, "2024-12-31")
        self.assertEqual(record["净利润"], "747.34亿")

    def test_financial_freshness_exposes_source_mapping_and_current_eligibility(self):
        metadata = _market_metadata(
            "[TOOL_OK]\n"
            "retrieved_at=2026-08-03T10:00:00+10:00\n"
            "data_source=AKShare/THS financial abstract\n"
            "report_period=2026-03-31\n"
            "report_period_source_field=报告期\n"
            "report_type=annual",
            "financial-indicators",
        )
        freshness = metadata["freshness"]
        self.assertEqual(freshness["data_source"], "AKShare/THS financial abstract")
        self.assertEqual(freshness["report_period_source_field"], "报告期")
        self.assertTrue(freshness["usable_for_current_conclusion"])

    def test_evidence_card_does_not_label_stale_financials_as_current(self):
        cards = build_evidence_cards([{
            "tool": "financial-indicators",
            "ok": True,
            "result_ref": "runtime:tool-result:financial-indicators:abc",
            "tool_metadata": {"data_source": "AKShare/THS financial abstract"},
            "freshness": {
                "status": "stale",
                "report_period": "2024-12-31",
                "age_days": 580,
                "usable_for_current_conclusion": False,
            },
        }])
        self.assertEqual(cards[0]["freshness"], "stale")
        self.assertFalse(cards[0]["usable_for_current_conclusion"])


if __name__ == "__main__":
    unittest.main()
