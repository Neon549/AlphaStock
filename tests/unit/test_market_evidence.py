import unittest
from unittest.mock import patch

from agent_runtime.context.compaction import persist_tool_result
from market.evidence import build_market_evidence_record, extract_market_fields


class MarketEvidenceTests(unittest.TestCase):
    def test_quote_result_becomes_typed_structured_snapshot(self):
        content = (
            "[TOOL_OK]\n"
            "tool=market-price\n"
            "symbol=600519\n"
            "retrieved_at=2026-08-15T10:20:30+10:00\n"
            "股票名称：贵州茅台\n"
            "最新价：1,500.50\n"
            "涨跌幅：2.5%\n"
            "成交量：100000\n"
            "数据来源：Tushare日线行情\n"
        )

        record = build_market_evidence_record("market-price", "600519", content)

        self.assertIsNotNone(record)
        self.assertEqual(record["evidence_type"], "quote")
        self.assertEqual(record["payload"]["price"], 1500.5)
        self.assertEqual(record["payload"]["change_pct"], 2.5)
        self.assertEqual(record["source"], "Tushare日线行情")
        self.assertEqual(record["quality_status"], "valid")

    def test_financial_result_keeps_period_and_financial_fields(self):
        content = (
            "[TOOL_OK]\n"
            "retrieved_at=2026-08-15T10:20:30+10:00\n"
            "report_period=2025-12-31\n"
            "report_type=annual\n"
            "营业总收入：1,234.5亿元\n"
            "净利润：300.2亿元\n"
            "ROE：12.3%\n"
            "数据来源：AKShare同花顺财务摘要\n"
        )

        record = build_market_evidence_record("financial-indicators", "600519", content)

        self.assertEqual(record["evidence_type"], "financial_indicator")
        self.assertEqual(record["period_end"], "2025-12-31")
        self.assertEqual(record["payload"]["report_type"], "annual")
        self.assertEqual(record["payload"]["roe"], 12.3)
        self.assertEqual(record["quality_status"], "valid")

    def test_errors_and_unsupported_tools_are_not_market_evidence(self):
        self.assertIsNone(build_market_evidence_record("market-price", "600519", "[TOOL_ERROR] unavailable"))
        self.assertIsNone(build_market_evidence_record("stock-news", "600519", "news"))
        self.assertEqual(extract_market_fields("最新价：100\n涨跌幅：-1.2%\n")["price"], "100")

    @patch("control_plane.observability.register_tool_artifact")
    def test_tool_artifact_carries_structured_market_evidence(self, register):
        content = (
            "[TOOL_OK]\nretrieved_at=2026-08-15T10:20:30+10:00\n"
            "股票名称：贵州茅台\n最新价：1500.50\n数据来源：fixture\n"
        )

        result_ref = persist_tool_result(
            tool="market-price",
            content=content,
            source_kind="market_evidence",
            citations=[],
            stock_code="600519",
        )

        artifact = register.call_args.args[1]
        self.assertEqual(artifact["market_evidence"]["evidence_id"], f"market:quote:600519:{artifact['market_evidence']['content_sha256'][:24]}")
        self.assertEqual(artifact["market_evidence"]["result_ref"], result_ref)

    def test_history_result_contains_rows_and_period_end(self):
        content = (
            "[TOOL_OK]\nretrieved_at=2026-08-15T10:20:30+10:00\n"
            "最近2天K线数据\n期间最高价：101.00\n期间最低价：98.00\n"
            "最新收盘价：100.00\n数据来源：fixture\n\n"
            "最近10日明细：\n日期 开盘 收盘 最高 最低 成交量 涨跌幅\n"
            "2026-08-14 99.00 100.00 101.00 98.00 1000 1.01\n"
            "2026-08-15 100.00 99.00 100.50 97.50 1200 -1.00\n"
        )

        record = build_market_evidence_record("market-history", "600519", content)

        self.assertEqual(record["evidence_type"], "daily_history")
        self.assertEqual(record["period_end"], "2026-08-15")
        self.assertEqual(record["payload"]["history_row_count"], 2)
        self.assertEqual(record["payload"]["history"][1]["close"], 99)
        self.assertEqual(record["quality_status"], "valid")


if __name__ == "__main__":
    unittest.main()
