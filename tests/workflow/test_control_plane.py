import unittest

from control_plane.contracts import AgentEvent, TriggerType
from control_plane.gateway import Gateway
from control_plane.investment_runtime import InvestmentRuntime
from agent_runtime.workflows.runtime import PythonInvestmentRuntime


class _Runtime:
    def __init__(self):
        self.events = []

    def run(self, event):
        self.events.append(event)
        from control_plane.contracts import AgentRunResult
        return AgentRunResult("run-1", "test", {"ok": True})


class _Store:
    def __init__(self):
        self.accepted = []
        self.runs = []

    def try_accept_event(self, event):
        self.accepted.append(event.event_id)
        return True

    def record_run(self, event, result):
        self.runs.append((event.event_id, result.run_id))

    def record_failure(self, event, error):
        raise AssertionError(f"unexpected failure: {error}")


class ControlPlaneTests(unittest.TestCase):
    def test_runtime_rejects_unknown_workflow_backend(self):
        with self.assertRaises(ValueError):
            InvestmentRuntime(workflow_runtime="unknown")

    def test_runtime_can_select_the_python_workflow_backend(self):
        runner = InvestmentRuntime(workflow_runtime="python")._workflow()
        self.assertIsInstance(runner.__self__, PythonInvestmentRuntime)

    def test_runtime_uses_python_workflow_by_default(self):
        runner = InvestmentRuntime()._workflow()
        self.assertIsInstance(runner.__self__, PythonInvestmentRuntime)

    def test_gateway_deduplicates_an_event_without_executing_runtime_twice(self):
        runtime = _Runtime()
        gateway = Gateway(runtime)
        event = AgentEvent(TriggerType.MESSAGE, "分析 600519", event_id="same-event")
        self.assertEqual(gateway.dispatch(event).route, "test")
        self.assertEqual(gateway.dispatch(event).route, "test")
        self.assertEqual(len(runtime.events), 1)

    def test_gateway_persists_only_the_single_accepted_run(self):
        runtime = _Runtime()
        store = _Store()
        gateway = Gateway(runtime, store=store)
        event = AgentEvent(TriggerType.HTTP, "分析 600519", event_id="persisted-event")
        gateway.dispatch(event)
        gateway.dispatch(event)
        self.assertEqual(store.accepted, ["persisted-event"])
        self.assertEqual(store.runs, [("persisted-event", "run-1")])

    def test_runtime_keeps_discussion_outside_the_workflow(self):
        calls = []
        runtime = InvestmentRuntime(
            intent_parser=lambda _: {"intent": 1, "reply_hint": None},
            workflow_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
            discussion_runner=lambda _: "discussion reply",
        )
        result = runtime.run(AgentEvent(TriggerType.MESSAGE, "聊聊银行业"))
        self.assertEqual(result.route, "discussion")
        self.assertEqual(result.payload["content"], "discussion reply")
        self.assertEqual(calls, [])

    def test_runtime_preserves_session_and_uses_server_selected_stock_code(self):
        calls = []

        def workflow(stock_code, **kwargs):
            calls.append((stock_code, kwargs))
            return {"publish_status": "requires_human_review", "final_decision": "draft"}

        runtime = InvestmentRuntime(
            intent_parser=lambda _: {"intent": 2, "stock_code": "600519", "stock_name": "贵州茅台", "analyst_focus": "technical"},
            workflow_runner=workflow,
            skill_selector=lambda *args, **kwargs: [],
            skill_executor=lambda *args, **kwargs: {},
        )
        result = runtime.run(AgentEvent(TriggerType.MESSAGE, "分析茅台", session_id="session-1"))
        self.assertEqual(result.route, "investment_workflow")
        self.assertEqual(calls[0][0], "600519")
        self.assertEqual(calls[0][1]["session_id"], "session-1")


if __name__ == "__main__":
    unittest.main()
