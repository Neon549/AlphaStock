from __future__ import annotations

import unittest

from agent_learning.dataset_export import candidate_to_training_row
from agent_learning.trace_evaluation import build_learning_candidate, evaluate_agent_run
from control_plane.contracts import AgentEvent, AgentRunResult, TriggerType


def _reviewable_result(*, trace: list[dict] | None = None) -> AgentRunResult:
    return AgentRunResult(
        run_id="run-reviewable",
        route="investment_agent_loop",
        payload={
            "decision": "Maintain a cautious research stance.",
            "publish_status": "requires_human_review",
            "human_review_required": True,
            "selected_skills": ["stock-analysis@v1"],
            "run_metrics": {"tool_call_count": 1},
            "workflow_result": {
                "publish_status": "requires_human_review",
                "human_review_required": True,
                "evidence_gate": {"passed": True, "evidence_refs": ["tool-price-1"]},
            },
        },
        trace=trace or [
            {"event": "intent_parsed", "intent": 2},
            {"event": "route_selected", "route": "investment_agent_loop"},
            {
                "event": "research_harness",
                "detail": {"event": "skill_result", "skill": "market-price", "ok": True, "result_ref": "tool-price-1"},
            },
        ],
    )


class AgentLearningTests(unittest.TestCase):
    def test_reviewable_run_passes_deterministic_rubrics(self):
        event = AgentEvent(TriggerType.MESSAGE, "analyse 600519", event_id="event-reviewable")
        evaluation = evaluate_agent_run(event, _reviewable_result())

        self.assertEqual(evaluation["outcome"], "passed")
        self.assertEqual(evaluation["badcase_types"], [])
        self.assertEqual(evaluation["score"], 1.0)

    def test_safe_block_is_not_a_training_candidate(self):
        event = AgentEvent(
            TriggerType.MESSAGE,
            "analyse 600519",
            event_id="event-blocked",
            metadata={"learning_capture": True},
        )
        result = _reviewable_result()
        result.payload["publish_status"] = "blocked"
        result.payload["human_review_required"] = False
        result.payload["workflow_result"] = {
            "publish_status": "blocked",
            "evidence_gate": {"passed": False},
        }

        evaluation = evaluate_agent_run(event, result)
        self.assertEqual(evaluation["outcome"], "safe_blocked")
        self.assertIn("safe_output_block", evaluation["badcase_types"])
        self.assertIsNone(build_learning_candidate(event, result, evaluation))

    def test_execution_failure_is_recorded_as_a_badcase(self):
        event = AgentEvent(TriggerType.MESSAGE, "analyse 600519", event_id="event-failed")
        result = _reviewable_result(trace=[
            {"event": "intent_parsed", "intent": 2},
            {"event": "route_selected", "route": "investment_agent_loop"},
            {"event": "research_harness", "detail": {"event": "budget_exhausted"}},
        ])

        evaluation = evaluate_agent_run(event, result)
        self.assertEqual(evaluation["outcome"], "failed")
        self.assertIn("budget_exhausted", evaluation["badcase_types"])

    def test_trajectory_capture_requires_explicit_opt_in(self):
        result = _reviewable_result()
        no_opt_in = AgentEvent(TriggerType.MESSAGE, "analyse 600519", event_id="event-no-opt-in")
        opted_in = AgentEvent(
            TriggerType.MESSAGE,
            "analyse 600519",
            event_id="event-opt-in",
            metadata={"learning_capture": True},
        )

        self.assertIsNone(build_learning_candidate(no_opt_in, result))
        candidate = build_learning_candidate(opted_in, result)
        self.assertEqual(candidate["status"], "pending_review")
        self.assertEqual(candidate["candidate_type"], "trajectory")
        self.assertEqual(candidate["sample"]["prompt"], "analyse 600519")

    def test_only_human_approved_sft_or_dpo_records_export(self):
        sft = candidate_to_training_row({
            "candidate_id": "candidate-sft",
            "run_id": "run-1",
            "candidate_type": "sft",
            "status": "approved",
            "reviewer": "reviewer-1",
            "sample": {"prompt": "question", "chosen": "reviewed answer"},
        })
        self.assertEqual(sft["messages"][1]["content"], "reviewed answer")

        alpaca_sft = candidate_to_training_row({
            "candidate_id": "candidate-sft-alpaca",
            "run_id": "run-1",
            "candidate_type": "sft",
            "status": "approved",
            "sample": {"prompt": "question", "chosen": "reviewed answer"},
        }, output_format="llamafactory-alpaca")
        self.assertEqual(alpaca_sft, {
            "instruction": "You are a governed research agent. Follow the task constraints, distinguish evidence from assumptions, and return only the human-reviewed answer.",
            "input": "question",
            "output": "reviewed answer",
        })

        with self.assertRaisesRegex(ValueError, "trajectory"):
            candidate_to_training_row({
                "candidate_id": "candidate-trajectory",
                "run_id": "run-2",
                "candidate_type": "trajectory",
                "status": "approved",
                "sample": {"prompt": "question", "chosen": "answer"},
            })


if __name__ == "__main__":
    unittest.main()
