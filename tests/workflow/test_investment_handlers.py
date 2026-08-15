import unittest
from unittest.mock import Mock, patch

from agent_runtime.workflows import investment_handlers


class TraderAndOutputGateHandlerTests(unittest.TestCase):
    @patch("agent_runtime.workflows.investment_handlers._get_long_term_memory")
    @patch("agent_runtime.workflows.investment_handlers._get_trader_model")
    def test_trader_uses_bounded_context_and_normalizes_position(self, get_model, get_memory):
        model = Mock()
        get_model.return_value = model
        get_memory.return_value.get_history.return_value = "previous approved decision"
        model.invoke.return_value = Mock(
            content="交易决策：买入\n建议仓位：80%\n操作价位：10.00-11.00元"
        )
        result = investment_handlers.trader_node(
            {
                "stock_code": "600519",
                "context_snapshot": {"analysts": {"technical": {"claim": "企稳"}}},
                "risk_assessment": "置信度：高",
                "memory_context": {"user_style": "conservative"},
            }
        )

        self.assertIn("建议仓位：20%", result["final_decision"])
        prompt = model.invoke.call_args.args[0][0].content
        self.assertIn("structured evidence snapshot", prompt)
        self.assertIn("previous approved decision", prompt)
        self.assertIn("user_style", prompt)

    def test_output_gate_stays_deterministic_for_read_only_advice(self):
        result = investment_handlers.output_gate_node(
            {
                "fundamental_report": "[ANALYSIS_OK] cash flow improved",
                "technical_report": "[SKIPPED] analyst branch was not requested",
                "sentiment_report": "[SKIPPED] analyst branch was not requested",
                "final_decision": "决策：持有观望\n建议仓位：0%",
            }
        )

        self.assertEqual(result["publish_status"], "published")
        self.assertFalse(result["human_review_required"])
