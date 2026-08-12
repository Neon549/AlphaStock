import unittest

from control_plane.contracts import AgentEvent, AgentRunResult, TriggerType
from control_plane.investment_runtime import InvestmentRuntime
from control_plane.source_registry import SourceDefinition, SourceRegistry
from control_plane.triggers import heartbeat_event, source_changed_event


class SourceRefreshRuntimeTests(unittest.TestCase):
    def _event(self, source_type="financial_report"):
        registry = SourceRegistry()
        registry.register(
            SourceDefinition(
                source_id="cninfo:600519:annual",
                source_type=source_type,
                metadata={"affected_symbols": ["600519"]},
            )
        )
        observation = registry.observe(
            "cninfo:600519:annual", version="2025-annual", content="report"
        )
        return source_changed_event(observation)

    def test_source_event_enters_research_runtime_with_focus(self):
        calls = []

        def workflow(stock_code, **kwargs):
            calls.append((stock_code, kwargs["analyst_focus"]))
            return {
                "technical_report": "[SKIPPED]",
                "fundamental_report": "[ANALYSIS_OK] report evidence",
                "sentiment_report": "[SKIPPED]",
                "final_decision": "draft",
                "publish_status": "requires_human_review",
                "publish_reasons": [],
                "human_review_required": True,
            }

        runtime = InvestmentRuntime(
            intent_parser=lambda _: {"intent": 0, "stock_code": None},
            workflow_runner=workflow,
            skill_selector=lambda *args, **kwargs: [],
        )
        result = runtime.run(self._event())

        self.assertEqual(calls, [("600519", "fundamental")])
        self.assertEqual(result.route, "investment_workflow")
        self.assertTrue(result.payload["source_refresh"])
        self.assertEqual(result.payload["source_version"], "2025-annual")
        self.assertTrue(any(step["event"] == "source_change_received" for step in result.trace))

    def test_heartbeat_is_handled_without_llm_or_workflow(self):
        runtime = InvestmentRuntime(intent_parser=lambda _: (_ for _ in ()).throw(AssertionError()))
        result = runtime.run(heartbeat_event(job_name="test", observed_at=None))
        self.assertEqual(result.route, "heartbeat")
        self.assertEqual(result.payload["status"], "ok")

    def test_source_refresh_requires_one_affected_symbol(self):
        event = AgentEvent(
            trigger=TriggerType.SOURCE_CHANGE,
            content="source changed",
            event_id="multi-source-event",
            metadata={
                "event_type": "source.changed",
                "source_id": "provider:batch",
                "affected_symbols": ["600519", "000858"],
            },
        )
        runtime = InvestmentRuntime(intent_parser=lambda _: {"intent": 0, "stock_code": None})
        result = runtime.run(event)
        self.assertEqual(result.route, "source_refresh")
        self.assertEqual(result.payload["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
