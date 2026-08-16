import unittest
from unittest.mock import patch

from agent_runtime.agents.investment_harness import run_investment_agent_loop
from agent_runtime.planning.task_graph import build_task_dag
from agent_runtime.workflows.investment_handlers import SKIPPED


class _Response:
    def __init__(self, content):
        self.content = content


class _SequenceLlm:
    def __init__(self, outputs):
        self._outputs = iter(outputs)

    def invoke(self, _prompt):
        return _Response(next(self._outputs))


class _FailingLlm:
    def __init__(self, error):
        self.error = error

    def invoke(self, _prompt):
        raise self.error


class InvestmentAgentLoopTests(unittest.TestCase):
    def test_loop_dynamically_delegates_allowlisted_subagents(self):
        planner = _SequenceLlm([
            '{"action":"subagents","subagents":["technical-researcher","sentiment-researcher"],"reason":"need independent market and news views"}',
            '{"action":"final","reason":"two specialist reports are sufficient"}',
        ])
        final = _SequenceLlm(["bull evidence; bear risk; neutral stance"])
        calls = []

        def execute_subagent(name, *, state, granted):
            calls.append((name, state["stock_code"], granted))
            reports = {
                "technical-researcher": {
                    "technical_report": "[ANALYSIS_OK] technical evidence",
                },
                "sentiment-researcher": {
                    "sentiment_report": "[ANALYSIS_OK] sentiment evidence",
                },
            }
            return {
                "subagent": name,
                "ok": True,
                "content": f"[ANALYSIS_OK] {name} evidence",
                "updates": reports[name],
                "source_kind": "analyst_report",
                "status": "completed",
            }

        with patch(
            "agent_runtime.agents.investment_harness.trader_node",
            return_value={"final_decision": "draft with risks"},
        ):
            result = run_investment_agent_loop(
                {"stock_code": "600519", "analysis_query": "technical and sentiment analysis", "analyst_focus": "all"},
                planner_llm=planner,
                final_llm=final,
                subagent_executor=execute_subagent,
            )

        self.assertEqual([call[0] for call in calls], ["technical-researcher", "sentiment-researcher"])
        self.assertEqual(result["technical_report"], "[ANALYSIS_OK] technical evidence")
        self.assertEqual(result["sentiment_report"], "[ANALYSIS_OK] sentiment evidence")
        self.assertEqual(
            [event["event"] for event in result["agent_trace"][:2]],
            ["subagent_result", "subagent_result"],
        )
        self.assertEqual(result["publish_status"], "blocked")
        self.assertFalse(result["evidence_gate"]["passed"])

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
        self.assertEqual(result["publish_status"], "blocked")
        self.assertFalse(result["evidence_gate"]["passed"])
        self.assertEqual(result["agent_trace"][0]["event"], "skill_result")
        self.assertIn("neutral stance", result["bull_argument"])
        self.assertEqual(result["harness"]["profile"], "investment")
        self.assertEqual(result["harness"]["status"], "completed")

    def test_loop_creates_and_destroys_one_ephemeral_evidence_reviewer(self):
        planner = _SequenceLlm([
            '{"action":"skill","skill":"market-price","arguments":{},"reason":"need current evidence"}',
            '{"action":"create_subagent","template":"evidence-critic","objective":"检查现有证据的冲突和缺口","reason":"user asked for an evidence review"}',
            '{"action":"final","reason":"review complete"}',
        ])
        final = _SequenceLlm([
            "价格记录已获取，但仍需确认公告时间。",
            "research summary with evidence gap",
        ])

        def execute(skill, *, state, arguments, granted):
            self.assertEqual(skill, "market-price")
            return {
                "ok": True,
                "content": "timestamped price=100",
                "source_kind": "market_evidence",
                "freshness": {"status": "retrieved"},
            }

        with patch(
            "agent_runtime.agents.investment_harness.trader_node",
            return_value={"final_decision": "draft with risks"},
        ):
            result = run_investment_agent_loop(
                {
                    "stock_code": "600519",
                    "analysis_query": "先查价格，再核验证据冲突",
                    "technical_report": SKIPPED,
                    "fundamental_report": SKIPPED,
                    "sentiment_report": SKIPPED,
                },
                planner_llm=planner,
                final_llm=final,
                skill_executor=execute,
            )

        events = result["agent_trace"]
        created = next(item for item in events if item["event"] == "ephemeral_subagent_created")
        destroyed = next(item for item in events if item["event"] == "ephemeral_subagent_destroyed")
        self.assertEqual(created["template"], "evidence-critic")
        self.assertEqual(created["instance_id"], destroyed["instance_id"])
        review = next(item for item in result["research_evidence"] if item["source_kind"] == "ephemeral_review")
        self.assertTrue(review["tool"].startswith("subagent:ephemeral-evidence-critic-"))

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

    def test_task_dag_blocks_a_dependent_skill_until_its_prerequisite_succeeds(self):
        planner = _SequenceLlm([
            '{"action":"skill","skill":"backtest","arguments":{"strategy":"kdj_macd"},"reason":"user asked"}',
            '{"action":"skill","skill":"analysis","arguments":{"focuses":["technical"]},"reason":"required prerequisite"}',
            '{"action":"skill","skill":"backtest","arguments":{"strategy":"kdj_macd"},"reason":"dependency completed"}',
            '{"action":"final","reason":"all planned tasks complete"}',
        ])
        final = _SequenceLlm(["research summary"])
        calls = []
        task_plan = build_task_dag([
            {
                "task_id": "analysis-1", "intent": "investment_analysis", "depends_on": [],
                "slots": {"stock_code": "600519", "analyst_focus": "technical"},
            },
            {
                "task_id": "backtest-1", "intent": "backtest", "depends_on": ["analysis-1"],
                "slots": {"stock_code": "600519"},
            },
        ])

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
            result = run_investment_agent_loop(
                {
                    "stock_code": "600519",
                    "analysis_query": "analyse then backtest",
                    "analyst_focus": "technical",
                    "task_plan": task_plan,
                },
                planner_llm=planner,
                final_llm=final,
                skill_executor=execute,
            )

        self.assertEqual(calls, ["analysis", "backtest"])
        self.assertEqual(result["task_status"], {"analysis-1": "succeeded", "backtest-1": "succeeded"})
        self.assertTrue(any(item["event"] == "task_dependency_blocked" for item in result["agent_trace"]))

    def test_loop_requires_and_accepts_traceable_current_evidence(self):
        planner = _SequenceLlm([
            '{"action":"skill","skill":"market-price","arguments":{},"reason":"need current evidence"}',
            '{"action":"skill","skill":"analysis","arguments":{"focuses":["technical"]},"reason":"need analysis"}',
            '{"action":"final","reason":"evidence complete"}',
        ])
        final = _SequenceLlm(["research summary"])

        def execute(skill, *, state, arguments, granted):
            if skill == "market-price":
                return {
                    "ok": True, "content": "timestamped price", "source_kind": "market_evidence",
                    "freshness": {"status": "retrieved"},
                }
            return {
                "ok": True, "content": "[ANALYSIS_OK] technical evidence",
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
                {"stock_code": "600519", "analysis_query": "technical analysis", "analyst_focus": "technical"},
                planner_llm=planner, final_llm=final, skill_executor=execute,
            )
        self.assertEqual(result["publish_status"], "published")
        self.assertTrue(result["evidence_gate"]["passed"])

    def test_parent_harness_persists_typed_skill_failure_for_the_next_planner_turn(self):
        planner = _SequenceLlm([
            '{"action":"skill","skill":"market-price","arguments":{},"reason":"need quote"}',
            '{"action":"final","reason":"quote is unavailable; preserve the gap"}',
        ])
        final = _SequenceLlm(["research summary with a stated evidence gap"])

        def execute(skill, *, state, arguments, granted):
            self.assertEqual(skill, "market-price")
            return {"ok": False, "content": "[TOOL_ERROR] missing stock code"}

        with patch(
            "agent_runtime.agents.investment_harness.trader_node",
            return_value={"final_decision": "draft with risks"},
        ):
            result = run_investment_agent_loop(
                {
                    "stock_code": "600519",
                    "analysis_query": "technical analysis",
                    "analyst_focus": "technical",
                    "technical_report": "[ANALYSIS_OK] existing analyst report",
                    # Keep the test on the parent-Harness path.  Empty
                    # non-requested reports are correctly treated as failed
                    # analyst branches by the workflow and would trigger the
                    # unrelated concrete analyst fallback (and its model
                    # dependency) during this unit-sized integration test.
                    "fundamental_report": SKIPPED,
                    "sentiment_report": SKIPPED,
                },
                planner_llm=planner,
                final_llm=final,
                skill_executor=execute,
            )

        failure_event = next(item for item in result["agent_trace"] if item["event"] == "skill_result")
        self.assertEqual(failure_event["tool_failure"]["error_type"], "INVALID_ARGUMENT")
        self.assertEqual(failure_event["attempts"], 1)
        self.assertEqual(result["research_evidence"][0]["tool_failure"]["next_action"], "repair_parameters_or_ask_user")

    def test_parent_harness_blocks_safely_when_the_planner_model_is_unavailable(self):
        result = run_investment_agent_loop(
            {
                "stock_code": "600519",
                "analysis_query": "technical analysis",
                "analyst_focus": "technical",
            },
            planner_llm=_FailingLlm(ConnectionError("model service unavailable")),
            final_llm=_SequenceLlm(["not reached"]),
        )

        self.assertEqual(result["publish_status"], "blocked")
        self.assertEqual(result["model_failures"][0]["error_type"], "UPSTREAM_UNAVAILABLE")
        self.assertTrue(any(item["event"] == "planner_model_unavailable" for item in result["agent_trace"]))


if __name__ == "__main__":
    unittest.main()
