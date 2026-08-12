import unittest
from unittest.mock import Mock

from control_plane.contracts import AgentRunResult
from control_plane.source_registry import SourceDefinition, SourceRegistry
from control_plane.source_watcher import SourceWatcher


class SourceWatcherTests(unittest.TestCase):
    def test_unchanged_source_does_not_dispatch(self):
        registry = SourceRegistry()
        registry.register(
            SourceDefinition(
                source_id="eastmoney:600519:news",
                source_type="news",
                metadata={"affected_symbols": ["600519"]},
            )
        )
        gateway = Mock()
        gateway.dispatch.return_value = AgentRunResult("run-1", "source_refresh", {"status": "success"})
        watcher = SourceWatcher(registry, gateway)

        first = watcher.observe_and_dispatch("eastmoney:600519:news", version="v1")
        second = watcher.observe_and_dispatch("eastmoney:600519:news", version="v1")

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        gateway.dispatch.assert_called_once()
        event = gateway.dispatch.call_args.args[0]
        self.assertEqual(event.metadata["event_type"], "source.changed")
        self.assertEqual(event.metadata["affected_symbols"], ["600519"])

    def test_durable_store_can_suppress_a_revision_after_process_restart(self):
        class Store:
            def __init__(self):
                self.accepted = set()

            def ensure_source(self, source):
                return None

            def accept_change(self, source, observation):
                if observation.dedupe_key in self.accepted:
                    return False
                self.accepted.add(observation.dedupe_key)
                return True

        store = Store()
        registry_one = SourceRegistry()
        registry_one.register(
            SourceDefinition(
                source_id="cninfo:600519:annual",
                source_type="financial_report",
                metadata={"affected_symbols": ["600519"]},
            )
        )
        gateway = Mock()
        gateway.dispatch.return_value = AgentRunResult("run-1", "source_refresh", {"status": "success"})
        SourceWatcher(registry_one, gateway, store).observe_and_dispatch(
            "cninfo:600519:annual", version="2025-annual", content="v1"
        )

        # A new watcher process has no in-memory watermark, but the durable
        # store still rejects the same source revision.
        registry_two = SourceRegistry()
        registry_two.register(
            SourceDefinition(
                source_id="cninfo:600519:annual",
                source_type="financial_report",
                metadata={"affected_symbols": ["600519"]},
            )
        )
        second = SourceWatcher(registry_two, gateway, store).observe_and_dispatch(
            "cninfo:600519:annual", version="2025-annual", content="v1"
        )
        self.assertIsNone(second)
        self.assertEqual(gateway.dispatch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
