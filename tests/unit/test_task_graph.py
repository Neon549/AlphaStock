from __future__ import annotations

import unittest

from agent_runtime.planning.task_graph import TaskGraphError, build_task_dag, execute_task_dag


def _task(task_id: str, intent: str, *, depends_on: list[str] | None = None, code: str | None = "600519", confirm: bool = False):
    return {
        "task_id": task_id,
        "intent": intent,
        "depends_on": depends_on or [],
        "slots": {"stock_code": code} if code else {},
        "requires_confirmation": confirm,
    }


class TaskGraphTests(unittest.TestCase):
    def test_parallel_tasks_share_a_topological_stage(self):
        plan = build_task_dag([
            _task("analysis-1", "investment_analysis"),
            _task("backtest-1", "backtest"),
        ])

        self.assertEqual(plan["stages"], [["analysis-1", "backtest-1"]])
        self.assertEqual(plan["runnable_stages"], [["analysis-1", "backtest-1"]])

    def test_dependency_creates_a_later_stage(self):
        plan = build_task_dag([
            _task("analysis-1", "investment_analysis"),
            _task("backtest-1", "backtest", depends_on=["analysis-1"]),
        ])

        self.assertEqual(plan["stages"], [["analysis-1"], ["backtest-1"]])
        self.assertEqual(plan["edges"], [{"from": "analysis-1", "to": "backtest-1"}])

    def test_confirmation_task_never_reaches_executor(self):
        plan = build_task_dag([
            _task("analysis-1", "investment_analysis"),
            _task("trade-1", "trade_action", depends_on=["analysis-1"], confirm=True),
        ])
        called = []

        result = execute_task_dag(plan, lambda task: called.append(task["task_id"]) or {"ok": True})

        self.assertEqual(called, ["analysis-1"])
        self.assertEqual(result["task_status"]["analysis-1"], "succeeded")
        self.assertEqual(result["task_status"]["trade-1"], "awaiting_confirmation")

    def test_failed_dependency_blocks_downstream_execution(self):
        plan = build_task_dag([
            _task("analysis-1", "investment_analysis"),
            _task("backtest-1", "backtest", depends_on=["analysis-1"]),
        ])
        called = []

        def executor(task):
            called.append(task["task_id"])
            return {"ok": False}

        result = execute_task_dag(plan, executor)

        self.assertEqual(called, ["analysis-1"])
        self.assertEqual(result["task_status"]["analysis-1"], "failed")
        self.assertEqual(result["task_status"]["backtest-1"], "blocked_dependency")

    def test_cycles_and_unknown_dependencies_are_rejected(self):
        with self.assertRaises(TaskGraphError):
            build_task_dag([
                _task("analysis-1", "investment_analysis", depends_on=["backtest-1"]),
                _task("backtest-1", "backtest", depends_on=["analysis-1"]),
            ])
        with self.assertRaises(TaskGraphError):
            build_task_dag([_task("analysis-1", "investment_analysis", depends_on=["missing"])])


if __name__ == "__main__":
    unittest.main()
