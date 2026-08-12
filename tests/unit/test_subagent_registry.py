import unittest

from agent_runtime.agents.subagents import (
    SubagentRegistry,
    SubagentResult,
    SubagentSpec,
    SubagentTask,
)


class SubagentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.spec = SubagentSpec(
            name="safe-researcher",
            description="Test-only bounded specialist",
            model_profile="inherit",
            allowed_tools=("market-price",),
            max_turns=1,
            permissions=("market:read",),
            output_key="technical_report",
        )

        def run(task):
            return SubagentResult(
                subagent="safe-researcher",
                ok=True,
                content=f"[ANALYSIS_OK] {task.stock_code}",
                updates={"technical_report": "[ANALYSIS_OK] signal"},
            )

        self.registry = SubagentRegistry(
            specs=(self.spec,),
            runners={"safe-researcher": run},
        )
        self.task = SubagentTask(stock_code="600519", request_query="technical only")

    def test_registry_exposes_only_authorised_specs(self):
        self.assertEqual(
            self.registry.list_available(
                granted_permissions=set(), has_session_document=False
            ),
            [],
        )
        available = self.registry.list_available(
            granted_permissions={"market:read"}, has_session_document=False
        )
        self.assertEqual([item["name"] for item in available], ["safe-researcher"])
        self.assertEqual(available[0]["allowed_tools"], ["market-price"])

    def test_spawn_checks_permissions_and_returns_typed_result(self):
        with self.assertRaises(PermissionError):
            self.registry.spawn(
                "safe-researcher", self.task, granted_permissions=set()
            )

        result = self.registry.spawn(
            "safe-researcher", self.task, granted_permissions={"market:read"}
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.updates["technical_report"], "[ANALYSIS_OK] signal")
        self.assertEqual(result.trace["allowed_tools"], ["market-price"])

    def test_batch_preserves_parent_requested_order(self):
        results = self.registry.spawn_many(
            ["safe-researcher"],
            self.task,
            granted_permissions={"market:read"},
        )
        self.assertEqual([result.subagent for result in results], ["safe-researcher"])


if __name__ == "__main__":
    unittest.main()
