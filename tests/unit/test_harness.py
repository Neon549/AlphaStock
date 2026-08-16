import unittest
from unittest.mock import patch

from agent_runtime.harness import Harness, MemoryStore, Profile, RESEARCH, Sandbox, ToolSpec
from agent_runtime.harness.store import SafeStore


class _UnavailableStore:
    def save(self, _state):
        raise RuntimeError("database unavailable")

    def load(self, _run_id):
        raise RuntimeError("database unavailable")


class HarnessTests(unittest.TestCase):
    def test_checkpoint_rollback_and_resume_keep_the_audit_log(self):
        store = MemoryStore()
        harness = Harness(store=store)
        run = harness.open("research", {"stock_code": "600519"}, run_id="run-1")
        run.state.data["plan"] = "price"
        first = run.checkpoint("plan_created")
        run.state.data["plan"] = "history"
        run.record("plan_changed")
        run.checkpoint("plan_changed")

        run.rollback(first)
        self.assertEqual(run.state.data["plan"], "price")
        self.assertTrue(any(item["event"] == "rollback" for item in run.state.events))

        resumed = harness.open("research", run_id="run-1", resume=True)
        self.assertEqual(resumed.state.status.value, "running")
        self.assertEqual(resumed.state.data["plan"], "price")
        self.assertGreaterEqual(len(resumed.state.checkpoints), 3)

    def test_gateway_checkpoints_evidence_and_uses_profile_tool(self):
        store = MemoryStore()
        run = Harness(store=store).open("research", {"stock_code": "600519"}, run_id="run-2")
        tool = run.profile.tool("market-price")
        self.assertIsNotNone(tool)

        result = run.tools.call(
            run,
            tool=tool,
            granted={"market:read"},
            arguments={},
            cache_key="unit:market-price",
            invoke=lambda: {
                "ok": True,
                "content": "price=100",
                "citations": [{"evidence_id": "market:unit:1"}],
                "source_kind": "market_evidence",
            },
            stock_code="600519",
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["result_ref"].startswith("runtime:tool-result:market-price:"))
        persisted = store.load("run-2")
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.data["observations"][0]["result_ref"], result["result_ref"])
        self.assertTrue(any(item["event"] == "tool_finished" for item in persisted.events))

    def test_full_access_never_allows_raw_commands_or_profile_side_effects(self):
        sandbox = Sandbox(mode="full_access")
        profile = Profile(
            name="unsafe-test",
            max_steps=1,
            tools=(ToolSpec("trade-evaluate", "market:read", "unsafe"),),
        )
        decision = sandbox.check(profile, profile.tools[0], granted={"market:read"})

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.stage, "immutable")
        self.assertFalse(sandbox.check_command("echo should-not-run").allowed)

    def test_network_kill_switch_stops_registered_market_tools(self):
        with patch.dict("os.environ", {"ALPHASTOCK_SANDBOX_NETWORK": "deny"}):
            decision = Sandbox().check(
                RESEARCH,
                RESEARCH.tool("market-price"),
                granted={"market:read"},
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.stage, "network")

    def test_actor_approval_mode_is_snapshotted_once_per_run(self):
        with patch(
            "agent_runtime.harness.run.get_approval_mode",
            return_value={"mode": "assist"},
        ) as get_mode:
            run = Harness(store=MemoryStore()).open(
                "research", {"stock_code": "600519", "actor_id": "neon"}
            )

        self.assertEqual(run.state.data["approval_mode"], "assist")
        self.assertEqual(run.summary()["sandbox_mode"], "assist")
        get_mode.assert_called_once_with("neon")

    def test_store_outage_falls_back_without_losing_the_session(self):
        store = SafeStore(primary=_UnavailableStore(), fallback=MemoryStore())
        run = Harness(store=store).open("research", {"stock_code": "600519"}, run_id="run-3")

        restored = store.load("run-3")
        self.assertIsNotNone(restored)
        self.assertTrue(any(item["event"] == "session_store_degraded" for item in restored.events))


if __name__ == "__main__":
    unittest.main()
