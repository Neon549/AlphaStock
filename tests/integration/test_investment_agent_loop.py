import unittest
from unittest.mock import patch

from agent_runtime.agents.investment_harness import run_investment_agent_loop


class _Response:
    def __init__(self, content):
        self.content = content


class _SequenceLlm:
    def __init__(self, outputs):
        self._outputs = iter(outputs)

    def invoke(self, _prompt):
        return _Response(next(self._outputs))


class InvestmentAgentLoopTests(unittest.TestCase):
    def test_loop_selects_a_skill_then_returns_a_governed_draft(self):
        planner = _SequenceLlm([
            '{"action":"skill","skill":"analysis","arguments":{"focuses":["technical"]},"reason":"requested focus"}',
            '{"action":"final","reason":"technical evidence is sufficient"}',
        ])
        final = _SequenceLlm(["bull evidence; bear risk; neutral stance"])
        calls = []

        def execute(skill, *, state, arguments, granted):
            calls.append((skill, arguments, state["stock_code"], granted))
            return {
                "ok": True,
                "content": "[ANALYSIS_OK] technical evidence",
                "updates": {
                    "technical_report": "[ANALYSIS_OK] technical evidence",
                    "fundamental_report": "[SKIPPED] not requested",
                    "sentiment_report": "[SKIPPED] not requested",
                },
                "source_kind": "analyst_report",
            }

        with patch(
            "agent_runtime.agents.investment_harness.trader_node",
            return_value={"final_decision": "draft with risks"},
        ):
            result = run_investment_agent_loop(
                {"stock_code": "600519", "analysis_query": "only technical analysis", "analyst_focus": "technical"},
                planner_llm=planner,
                final_llm=final,
                skill_executor=execute,
            )

        self.assertEqual(calls[0][0], "analysis")
        self.assertEqual(calls[0][1]["focuses"], ["technical"])
        self.assertEqual(result["publish_status"], "requires_human_review")
        self.assertEqual(result["agent_trace"][0]["event"], "skill_result")
        self.assertIn("neutral stance", result["bull_argument"])

    def test_backtest_is_not_run_until_the_planner_explicitly_selects_it(self):
        planner = _SequenceLlm([
            '{"action":"skill","skill":"backtest","arguments":{"strategy":"kdj_macd"},"reason":"user requested validation"}',
            '{"action":"skill","skill":"analysis","arguments":{"focuses":["technical"]},"reason":"need a report"}',
            '{"action":"final","reason":"enough"}',
        ])
        final = _SequenceLlm(["research summary"])
        calls = []

        def execute(skill, *, state, arguments, granted):
            calls.append(skill)
            if skill == "analysis":
                return {
                    "ok": True,
                    "content": "[ANALYSIS_OK] technical evidence",
                    "updates": {
                        "technical_report": "[ANALYSIS_OK] technical evidence",
                        "fundamental_report": "[SKIPPED] not requested",
                        "sentiment_report": "[SKIPPED] not requested",
                    },
                    "source_kind": "analyst_report",
                }
            return {"ok": True, "content": "historical backtest only", "source_kind": "backtest_evidence"}

        with patch(
            "agent_runtime.agents.investment_harness.trader_node",
            return_value={"final_decision": "draft with risks"},
        ):
            run_investment_agent_loop(
                {"stock_code": "600519", "analysis_query": "run a backtest", "analyst_focus": "technical"},
                planner_llm=planner,
                final_llm=final,
                skill_executor=execute,
            )
        self.assertEqual(calls, ["backtest", "analysis"])


if __name__ == "__main__":
    unittest.main()
