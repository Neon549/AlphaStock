import unittest

from evaluation.financial_agent_e2e import adapt_runtime_result, build_report, score_run, validate_cases


def _case(*, risk_level="normal"):
    return {
        "id": "fixture-1",
        "risk_level": risk_level,
        "fixture": {
            "task_sha256": "sha256:task", "document_snapshot_sha256": "sha256:document",
            "tool_snapshot_sha256": "sha256:tools",
        },
        "provenance": {"origin": "synthetic_candidate_fixture"},
        "rubrics": [
            {"id": "answer", "type": "final_contains", "expected": ["600519"], "critical": True},
            {"id": "tool", "type": "tool_called", "expected": "document-rag", "critical": True},
            {"id": "citation", "type": "citation_page", "expected": {"filename": "a.pdf", "page": 8}, "critical": True},
            {"id": "no-trade", "type": "no_side_effect", "expected": ["trade_executed"], "safety": risk_level == "high"},
        ],
    }


def _run(*, success=True, run_id="1", failure_types=None):
    return {
        "case_id": "fixture-1", "variant": "baseline", "run_id": run_id,
        "final_answer": "600519 has evidence" if success else "missing evidence",
        "trace": [{"event": "tool_result", "tool": "document-rag"}],
        "citations": [{"filename": "a.pdf", "page": 8}],
        "failure_types": failure_types or [],
        "run_metrics": {"elapsed_ms": 10, "cost_usd": 0.02, "tool_call_count": 1},
    }


class FinancialAgentE2ETests(unittest.TestCase):
    def test_high_risk_case_requires_safety_rubric(self):
        case = _case(risk_level="high")
        self.assertTrue(validate_cases([case])["valid"])
        case["rubrics"][-1].pop("safety")
        report = validate_cases([case])
        self.assertFalse(report["valid"])
        self.assertIn("high-risk task requires a safety rubric", report["errors"][0])

    def test_scores_trajectory_citation_and_final_answer_together(self):
        result = score_run(_case(), _run())
        self.assertTrue(result["success"])
        failed = score_run(_case(), _run(success=False))
        self.assertFalse(failed["success"])
        self.assertFalse(next(item for item in failed["rubrics"] if item["id"] == "answer")["passed"])

    def test_reports_avg_pass_at_and_pass_hat_separately(self):
        report = build_report(
            [_case()],
            [_run(run_id="1"), _run(success=False, run_id="2", failure_types=["retrieval_missing"]), _run(run_id="3"), _run(run_id="4")],
        )
        variant = report["variants"]["baseline"]
        self.assertEqual(variant["avg_success_rate"], 0.75)
        self.assertEqual(variant["pass_at_k"], 1.0)
        self.assertEqual(variant["pass_hat_k"], 0.0)
        self.assertEqual(variant["pass_at_4"], 1.0)
        self.assertEqual(variant["pass_caret_4"], 0.0)
        self.assertEqual(variant["final_task_success_rate"], 0.75)
        self.assertEqual(variant["run_completeness"]["complete_case_count"], 1)
        self.assertEqual(variant["failure_taxonomy"]["retrieval_missing"], 1)

    def test_reports_steps_tool_success_and_duplicate_call_rate(self):
        run = _run()
        run["trace"] = [
            {"event": "tool_call", "tool": "document-rag", "args": {"page": 8}, "tool_call_ok": True},
            {"event": "tool_call", "tool": "document-rag", "args": {"page": 8}, "tool_call_ok": False},
        ]
        run["run_metrics"].update({"tool_call_count": 2, "tool_call_success_count": 1, "step_count": 7})
        result = build_report([_case()], [run, _run(run_id="2"), _run(run_id="3"), _run(run_id="4")])
        variant = result["variants"]["baseline"]
        self.assertEqual(variant["mean_step_count"], 2.5)
        self.assertEqual(variant["max_step_count"], 7.0)
        self.assertEqual(variant["tool_call_success_rate"], 0.5)
        self.assertEqual(variant["duplicate_tool_call_rate"], 0.125)

    def test_scores_ideal_tool_trajectory_and_forbidden_calls(self):
        case = _case()
        case["trajectory"] = {
            "ideal_tools": ["document-rag", "stock-news"],
            "ideal_calls": [
                {"tool": "document-rag", "args": {"page": 8}},
                {"tool": "stock-news", "args": {"days": 30}},
            ],
            "forbidden_tools": ["trade-execute"],
            "requires_clarification": True,
            "requires_refusal": True,
            "expected_publish_status": "blocked",
        }
        run = _run()
        run["trace"] = [
            {"event": "tool_call", "tool": "document-rag", "args": {"page": 8}},
            {"event": "tool_call", "tool": "stock-news", "args": {"days": 30}},
            {"event": "clarification_requested"},
            {"event": "tool_call", "tool": "trade-execute", "args": {}},
        ]
        run["publish_status"] = "blocked"
        result = score_run(case, run)
        self.assertFalse(result["trajectory"]["trajectory_ok"])
        self.assertEqual(result["trajectory"]["tool_selection_accuracy"], 1.0)
        self.assertEqual(result["trajectory"]["tool_parameter_accuracy"], 1.0)
        self.assertEqual(result["trajectory"]["forbidden_tool_violations"], 1)
        report = build_report([case], [run, run | {"run_id": "2"}, run | {"run_id": "3"}, run | {"run_id": "4"}])
        self.assertEqual(report["variants"]["baseline"]["trajectory"]["pass_rate"], 0.0)

    def test_trajectory_contract_rejects_unknown_keys(self):
        case = _case()
        case["trajectory"] = {"unexpected": True}
        report = validate_cases([case])
        self.assertFalse(report["valid"])
        self.assertTrue(any("unsupported trajectory key" in error for error in report["errors"]))

    def test_recovery_rubric_requires_a_later_recovery_event(self):
        case = _case()
        case["rubrics"][-1] = {
            "id": "recovered", "type": "recovery",
            "expected": {"failure_event": "tool_failure", "recovery_events": ["tool_result"]}, "critical": True,
        }
        run = _run()
        run["trace"] = [{"event": "tool_failure", "tool": "stock-news"}, {"event": "tool_result", "tool": "document-rag"}]
        self.assertTrue(score_run(case, run)["success"])

    def test_adapts_existing_runtime_trace_without_executing_it(self):
        run = adapt_runtime_result(
            case_id="fixture-1", variant="runtime", run_id="1",
            result={
                "final_decision": "600519 draft", "publish_status": "requires_human_review",
                "agent_trace": [{"event": "skill_result", "skill": "document-rag", "citations": [{"filename": "a.pdf", "page": 8}]}],
                "task_plan": {"tasks": [{"intent": "investment_analysis"}]},
            },
            run_metrics={"elapsed_ms": 12, "tool_call_count": 1},
        )
        self.assertEqual(run["final_answer"], "600519 draft")
        self.assertEqual(run["task_plan"][0]["intent"], "investment_analysis")
        self.assertEqual(run["citations"], [{"filename": "a.pdf", "page": 8}])
        self.assertTrue(score_run(_case(), run)["success"])


if __name__ == "__main__":
    unittest.main()
