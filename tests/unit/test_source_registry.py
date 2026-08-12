import unittest
from datetime import datetime, timezone

from control_plane.source_registry import SourceDefinition, SourceRegistry
from control_plane.triggers import source_changed_event
from control_plane.contracts import TriggerType


class SourceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = SourceRegistry()
        self.registry.register(
            SourceDefinition(
                source_id="cninfo:600519:annual-report",
                source_type="financial_report",
                endpoint="https://example.invalid/report",
                metadata={"affected_symbols": ["600519"]},
            )
        )

    def test_first_revision_emits_stable_event(self):
        observation = self.registry.observe(
            "cninfo:600519:annual-report",
            version="2025-annual",
            content="report-v1",
            observed_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        )
        self.assertTrue(observation.changed)
        event = source_changed_event(observation)
        self.assertEqual(event.trigger, TriggerType.SOURCE_CHANGE)
        self.assertEqual(event.metadata["source_version"], "2025-annual")
        self.assertEqual(event.metadata["affected_symbols"], ["600519"])

    def test_same_revision_and_hash_is_not_emitted_twice(self):
        first = self.registry.observe(
            "cninfo:600519:annual-report", version="2025-annual", content="report-v1"
        )
        second = self.registry.observe(
            "cninfo:600519:annual-report", version="2025-annual", content="report-v1"
        )
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(first.event_id, second.event_id)
        with self.assertRaises(ValueError):
            source_changed_event(second)

    def test_same_version_with_corrected_content_is_a_new_revision(self):
        first = self.registry.observe(
            "cninfo:600519:annual-report", version="2025-annual", content="report-v1"
        )
        corrected = self.registry.observe(
            "cninfo:600519:annual-report", version="2025-annual", content="report-v2-corrected"
        )
        self.assertTrue(corrected.changed)
        self.assertNotEqual(first.event_id, corrected.event_id)


if __name__ == "__main__":
    unittest.main()
