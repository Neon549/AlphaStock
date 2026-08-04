import unittest

from agent_runtime.workflows.backtest_state_machine import run_backtest_workflow
from agent_runtime.workflows.runtime import PythonBacktestRuntime
from agent_runtime.compat.langgraph.trading_graph import should_continue_after_backtest
from backtest.strategy_knowledge import retrieve_backtest_knowledge


class BacktestStateMachineTests(unittest.TestCase):
    def test_normal_path_has_one_fixed_pass_through_all_handlers(self):
        calls = []

        def handler(name, update=None):
            def run(_state):
                calls.append(name)
                return update or {}

            return run

        state = run_backtest_workflow(
            {},
            {
                "backtest": handler("backtest", {"backtest_report": "[TOOL_OK] metrics"}),
                "interpreter": handler("interpreter", {"backtest_summary": "summary"}),
                "optimizer": handler("optimizer", {"optimization": "top-3"}),
            },
        )
        self.assertEqual(calls, ["backtest", "interpreter", "optimizer"])
        self.assertEqual(state["optimization"], "top-3")

    def test_tool_error_skips_optimizer(self):
        calls = []

        def backtest(_state):
            calls.append("backtest")
            return {"backtest_report": "[TOOL_ERROR] unavailable"}

        def interpreter(_state):
            calls.append("interpreter")
            return {"backtest_summary": "no result"}

        def optimizer(_state):
            calls.append("optimizer")
            return {}

        run_backtest_workflow({}, {"backtest": backtest, "interpreter": interpreter, "optimizer": optimizer})
        self.assertEqual(calls, ["backtest", "interpreter"])

    def test_runtime_builds_the_same_backtest_request_contract(self):
        handlers = {name: (lambda _state: {}) for name in ("backtest", "interpreter", "optimizer")}
        result = PythonBacktestRuntime(handlers).run("600519", strategy="rsi", initial_cash=200000)
        self.assertEqual(result["backtest_request"]["strategy"], "rsi")
        self.assertEqual(result["backtest_request"]["initial_cash"], 200000)

    def test_langgraph_compatibility_skips_optimisation_after_tool_error(self):
        self.assertEqual(
            should_continue_after_backtest({"backtest_report": "[TOOL_ERROR] unavailable"}),
            "end",
        )
        self.assertEqual(
            should_continue_after_backtest({"backtest_report": "[TOOL_OK] metrics"}),
            "optimizer",
        )

    def test_backtest_knowledge_is_local_and_strategy_specific(self):
        knowledge = retrieve_backtest_knowledge("kdj strategy sharpe drawdown")
        self.assertIn("KDJ strategy discipline", knowledge)
        self.assertIn("Risk-adjusted metrics", knowledge)

    def test_backtest_state_declares_optimizer_contract_fields(self):
        from agent_runtime.compat.langgraph.state import TradingState

        self.assertIn("backtest_optimizer_ran", TradingState.__annotations__)
        self.assertIn("backtest_optimizer_skipped", TradingState.__annotations__)
