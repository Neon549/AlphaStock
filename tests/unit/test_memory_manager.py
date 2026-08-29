import os
import unittest
from unittest.mock import patch

from control_plane.contracts import AgentEvent, TriggerType
from agent_runtime.memory.manager import (
    NullMemoryManager,
    _capture_session_id,
    _intake_capture_enabled,
    _safe_session_summary,
)


class MemoryManagerTests(unittest.TestCase):
    def test_session_summary_does_not_promote_unreviewed_decision_to_memory(self):
        summary = _safe_session_summary(
            {"intent": 2, "stock_code": "600519", "analyst_focus": "all"},
            {
                "draft_decision": "buy immediately",
                "context_snapshot": {
                    "unresolved_risks": ["stale price"],
                    "document_citations": [{"evidence_id": "doc:p12"}],
                },
            },
        )
        self.assertEqual(summary["last_stock_code"], "600519")
        self.assertEqual(summary["evidence_ids"], ["doc:p12"])
        self.assertNotIn("draft_decision", summary)

    def test_null_memory_is_safe_for_local_or_unit_runs(self):
        manager = NullMemoryManager()
        context = manager.load_context(AgentEvent(TriggerType.MESSAGE, "分析 600519"))
        self.assertEqual(context["preferences"], {})
        self.assertEqual(context["session"], {})
        self.assertEqual(context["recent_transcript"], [])

    def test_capture_is_opt_in_for_requests_without_a_session(self):
        event = AgentEvent(TriggerType.MESSAGE, "analyze 600519", metadata={})
        with patch.dict(os.environ, {"ALPHASTOCK_E2E_INTAKE_CAPTURE": "false"}, clear=False):
            self.assertFalse(_intake_capture_enabled(event))
            self.assertIsNone(_capture_session_id(event, "run-1"))
        with patch.dict(os.environ, {"ALPHASTOCK_E2E_INTAKE_CAPTURE": "true"}, clear=False):
            self.assertTrue(_intake_capture_enabled(event))
            self.assertEqual(_capture_session_id(event, "run-1"), "capture-run-1")

    def test_event_capture_opt_in_works_without_global_window(self):
        event = AgentEvent(
            TriggerType.MESSAGE,
            "analyze 600519",
            metadata={"learning_capture": True},
        )
        with patch.dict(os.environ, {"ALPHASTOCK_E2E_INTAKE_CAPTURE": "false"}, clear=False):
            self.assertEqual(_capture_session_id(event, "run-1"), "capture-run-1")


if __name__ == "__main__":
    unittest.main()
