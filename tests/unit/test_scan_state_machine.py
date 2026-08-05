import unittest

from agent_runtime.workflows.scan_state_machine import run_daily_scan


class DailyScanStateMachineTests(unittest.TestCase):
    def test_keeps_batch_running_and_filters_recommendations(self):
        candidates = [
            {"code": "000001", "name": "A", "j": 20},
            {"code": "000002", "name": "B", "j": 10},
            {"code": "000003", "name": "C", "j": 5},
        ]

        def scanner(base_start, strategy, top_n):
            self.assertEqual((base_start, strategy, top_n), ("20240101", "all", 5))
            return candidates

        def analyser(candidate):
            if candidate["code"] == "000003":
                raise RuntimeError("source unavailable")
            return {
                **candidate,
                "decision": "买入" if candidate["code"] == "000001" else "观望",
                "confidence": "高",
            }

        result = run_daily_scan(
            base_start="20240101", scanner=scanner, analyser=analyser
        )

        self.assertEqual(result["runtime"], "python_state_machine")
        self.assertEqual([row["code"] for row in result["final_recommendations"]], ["000001"])
        self.assertEqual(result["analysis_errors"][0]["stock_code"], "000003")


if __name__ == "__main__":
    unittest.main()
