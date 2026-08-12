import unittest

from control_plane.contracts import AgentEvent, TriggerType
from control_plane.investment_runtime import InvestmentRuntime


class HeartbeatRuntimeTests(unittest.TestCase):
    def test_heartbeat_does_not_load_model_or_workflow_dependencies(self):
        runtime = InvestmentRuntime()

        def fail_if_loaded():
            raise AssertionError("heartbeat must not initialise LLM/RAG dependencies")

        runtime._deps = fail_if_loaded
        result = runtime.run(
            AgentEvent(
                trigger=TriggerType.HEARTBEAT,
                content="agent heartbeat",
                event_id="heartbeat-test",
            )
        )

        self.assertEqual(result.route, "heartbeat")
        self.assertEqual(result.payload["status"], "ok")


if __name__ == "__main__":
    unittest.main()
