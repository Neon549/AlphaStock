import unittest
from unittest.mock import patch

from backtest.service import BacktestInputError, execute_backtest
from tools.backtest_tools import run_strategy_backtest


class BacktestServiceTests(unittest.TestCase):
    def test_invalid_stock_code_is_rejected_before_loading_data(self):
        with self.assertRaises(BacktestInputError):
            execute_backtest(stock_code="not-a-code")

    @patch("tools.backtest_tools.execute_backtest")
    def test_tool_is_an_adapter_over_the_shared_service(self, execute):
        execute.return_value = {"report_text": "metrics", "data_source": "tushare"}
        output = run_strategy_backtest.invoke({"stock_code": "600519"})
        self.assertIn("[TOOL_OK]", output)
        self.assertIn("data_source=tushare", output)
        execute.assert_called_once()
