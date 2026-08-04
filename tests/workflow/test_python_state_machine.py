import unittest

from agent_runtime.workflows.python_state_machine import run_fixed_workflow
from agent_runtime.workflows.runtime import PythonInvestmentRuntime
from agent_runtime.workflows.investment_handlers import default_handlers


class PythonStateMachineTests(unittest.TestCase):
    def test_normal_path_and_single_replan(self):
        calls = []
        def handler(name, update=None):
            def fn(_state):
                calls.append(name)
                return update or {}
            return fn
        handlers = {
            "policy_guard": handler("policy_guard"), "analysts": handler("analysts"),
            "context_snapshot": handler("context_snapshot"),
            "validation": handler("validation", {"replan_required": True}),
            "replan": handler("replan", {"replan_required": False}),
            "researcher": handler("researcher"), "trader": handler("trader"),
            "output_gate": handler("output_gate", {"publish_status": "requires_human_review"}),
            "abort": handler("abort"),
        }
        state = run_fixed_workflow({}, handlers)
        self.assertEqual(calls, ["policy_guard", "analysts", "context_snapshot", "validation", "replan", "context_snapshot", "validation", "researcher", "trader", "output_gate"])
        self.assertEqual(state["publish_status"], "requires_human_review")

    def test_policy_block_stops_before_analysts(self):
        calls = []
        def make(name, update=None):
            return lambda _state: (calls.append(name) or update or {})
        handlers = {name: make(name) for name in ["analysts", "context_snapshot", "validation", "replan", "researcher", "trader", "output_gate"]}
        handlers["policy_guard"] = make("policy_guard", {"publish_status": "blocked"})
        handlers["abort"] = make("abort")
        run_fixed_workflow({}, handlers)
        self.assertEqual(calls, ["policy_guard", "abort"])

    def test_runtime_uses_the_same_handler_contract(self):
        handlers = {name: (lambda _state: {}) for name in ["policy_guard", "analysts", "context_snapshot", "validation", "replan", "researcher", "trader", "output_gate", "abort"]}
        result = PythonInvestmentRuntime(handlers).run("600519")
        self.assertEqual(result["stock_code"], "600519")

    def test_runtime_normalizes_gateway_document_context(self):
        handlers = {name: (lambda _state: {}) for name in ["policy_guard", "analysts", "context_snapshot", "validation", "replan", "researcher", "trader", "output_gate", "abort"]}
        result = PythonInvestmentRuntime(handlers).run("600519", doc_context="page-level evidence")
        self.assertEqual(result["user_doc_context"], "page-level evidence")
        self.assertNotIn("doc_context", result)

    def test_default_handler_registry_contains_the_entire_main_flow(self):
        handlers = default_handlers()
        self.assertEqual(
            set(handlers),
            {
                "policy_guard", "analysts", "context_snapshot", "validation",
                "replan", "abort", "researcher", "trader", "output_gate",
            },
        )
