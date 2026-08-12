import os
import unittest
from unittest.mock import patch

from agent_runtime.agents.research_harness import _default_executor, _market_metadata, run_research_harness


class _Response:
    def __init__(self, content):
        self.content = content


class _SequenceLlm:
    def __init__(self, outputs):
        self.outputs = iter(outputs)

    def invoke(self, _prompt):
        return _Response(next(self.outputs))


class _FailingLlm:
    def __init__(self, error):
        self.error = error

    def invoke(self, _prompt):
        raise self.error


class _OverflowThenResponse:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("HTTP 413 context length exceeded")
        return _Response(self.response)


class ResearchHarnessTests(unittest.TestCase):
    def test_harness_binds_the_stock_and_returns_auditable_trace(self):
        planner = _SequenceLlm([
            '{"action":"tool","tool":"market-price","arguments":{},"reason":"verify price"}',
            '{"action":"final","reason":"enough evidence"}',
        ])
        final = _SequenceLlm(["bull: evidence; bear: risk; stance: neutral"])
        calls = []

        def execute(name, **kwargs):
            calls.append((name, kwargs["stock_code"]))
            return {"ok": True, "content": "price=100", "citations": []}

        result = run_research_harness(
            stock_code="600519",
            snapshot={"stock_code": "600519"},
            request_query="analyze 600519",
            planner_llm=planner,
            final_llm=final,
            tool_executor=execute,
        )
        self.assertEqual(calls, [("market-price", "600519")])
        self.assertEqual(result["trace"][0]["event"], "tool_result")
        self.assertTrue(result["trace"][0]["result_ref"].startswith("runtime:tool-result:market-price:"))
        self.assertEqual(result["trace"][1]["event"], "final")
        self.assertIn("stance", result["report"])

    def test_harness_refuses_unregistered_tools(self):
        planner = _SequenceLlm(['{"action":"tool","tool":"shell","arguments":{}}'])
        final = _SequenceLlm(["safe final"])
        result = run_research_harness(
            stock_code="600519", snapshot={}, planner_llm=planner, final_llm=final
        )
        self.assertEqual(result["trace"][0]["event"], "tool_denied")

    def test_harness_can_select_the_approved_memory_search_tool(self):
        planner = _SequenceLlm([
            '{"action":"tool","tool":"memory-search","arguments":{"query":"how to handle stale evidence"}}',
            '{"action":"final","reason":"memory located"}',
        ])
        final = _SequenceLlm(["safe final"])
        calls = []

        def execute(name, **kwargs):
            calls.append((name, kwargs["query"], kwargs["stock_code"]))
            return {"ok": True, "content": "approved policy", "citations": [{"evidence_id": "memory:policy:1"}]}

        result = run_research_harness(
            stock_code="600519", snapshot={}, planner_llm=planner,
            final_llm=final, tool_executor=execute,
        )
        self.assertEqual(calls, [("memory-search", "how to handle stale evidence", "600519")])
        self.assertEqual(result["trace"][0]["tool"], "memory-search")
        self.assertEqual(result["trace"][0]["citations"][0]["evidence_id"], "memory:policy:1")
        self.assertEqual(result["observations"][0]["source_kind"], "evidence")

    def test_harness_skips_a_duplicate_tool_request(self):
        planner = _SequenceLlm([
            '{"action":"tool","tool":"financial-indicators","arguments":{},"reason":"first"}',
            '{"action":"tool","tool":"financial-indicators","arguments":{},"reason":"duplicate"}',
            '{"action":"final","reason":"enough"}',
        ])
        final = _SequenceLlm(["safe final"])
        calls = []

        def execute(name, **kwargs):
            calls.append(name)
            return {"ok": True, "content": "report_period=2026-03-31", "citations": []}

        result = run_research_harness(
            stock_code="600519", snapshot={}, request_query="analyze 600519",
            planner_llm=planner, final_llm=final, tool_executor=execute,
        )
        self.assertEqual(calls, ["financial-indicators"])
        self.assertEqual(result["trace"][1]["event"], "duplicate_tool_skipped")

    def test_financial_metadata_marks_a_1998_report_as_stale(self):
        metadata = _market_metadata(
            "[TOOL_OK]\nretrieved_at=2026-08-03T10:00:00+10:00\nreport_period=1998-12-31",
            "financial-indicators",
        )
        self.assertEqual(metadata["freshness"]["status"], "stale")

    def test_harness_retries_once_with_emergency_compaction_after_413(self):
        planner = _OverflowThenResponse('{"action":"final","reason":"enough"}')
        final = _SequenceLlm(["safe final"])
        result = run_research_harness(
            stock_code="600519", snapshot={}, planner_llm=planner, final_llm=final
        )
        self.assertEqual(planner.calls, 2)
        self.assertEqual(result["trace"][0]["event"], "reactive_compact_retry")
        self.assertEqual(result["report"], "safe final")

    def test_harness_records_a_typed_deterministic_tool_failure_without_retrying(self):
        planner = _SequenceLlm([
            '{"action":"tool","tool":"market-price","arguments":{},"reason":"verify"}',
            '{"action":"final","reason":"cannot repair server-bound code"}',
        ])
        final = _SequenceLlm(["safe final with evidence gap"])
        calls = []

        def execute(name, **kwargs):
            calls.append(name)
            return {"ok": False, "content": "[TOOL_ERROR] missing stock code"}

        result = run_research_harness(
            stock_code="600519", snapshot={}, planner_llm=planner,
            final_llm=final, tool_executor=execute,
        )

        self.assertEqual(calls, ["market-price"])
        self.assertEqual(result["trace"][0]["tool_failure"]["error_type"], "INVALID_ARGUMENT")
        self.assertEqual(result["trace"][0]["tool_failure"]["next_action"], "repair_parameters_or_ask_user")
        self.assertEqual(result["trace"][0]["attempts"], 1)

    def test_harness_retries_a_transient_tool_failure_within_the_run_budget(self):
        planner = _SequenceLlm([
            '{"action":"tool","tool":"market-price","arguments":{},"reason":"verify"}',
            '{"action":"final","reason":"retrieved"}',
        ])
        final = _SequenceLlm(["safe final"])
        calls = []

        def execute(name, **kwargs):
            calls.append(name)
            if len(calls) == 1:
                raise TimeoutError("read timed out")
            return {"ok": True, "content": "retrieved_at=2026-08-11T09:00:00+10:00"}

        with patch.dict(os.environ, {"AGENT_TOOL_INITIAL_BACKOFF_SECONDS": "0"}):
            result = run_research_harness(
                stock_code="600519", snapshot={}, planner_llm=planner,
                final_llm=final, tool_executor=execute,
            )

        self.assertEqual(calls, ["market-price", "market-price"])
        self.assertEqual(result["trace"][0]["attempts"], 2)
        self.assertEqual(result["trace"][0]["retry_trace"][0]["failure"]["error_type"], "TIMEOUT")

    def test_harness_converts_model_unavailability_to_a_safe_research_abort(self):
        result = run_research_harness(
            stock_code="600519",
            snapshot={},
            planner_llm=_FailingLlm(ConnectionError("model service unavailable")),
            final_llm=_SequenceLlm(["not reached"]),
        )

        self.assertTrue(result["report"].startswith("[RESEARCH_ABORT]"))
        self.assertEqual(result["model_failures"][0]["error_type"], "UPSTREAM_UNAVAILABLE")
        self.assertEqual(result["trace"][0]["event"], "model_unavailable")

    @patch("agent_runtime.memory.index.search_memory")
    def test_default_memory_tool_marks_guidance_as_non_market_evidence(self, search_memory):
        search_memory.return_value = [{
            "evidence_id": "memory:governance.md:abc:0",
            "source_path": "governance.md",
            "chunk_index": 0,
            "content": "Never use stale prices as current evidence.",
        }]
        result = _default_executor(
            "memory-search", stock_code="600519", session_id=None,
            query="stale evidence", granted_permissions={"memory:read"},
        )
        self.assertEqual(result["source_kind"], "operational_memory")
        self.assertIn("Never use stale prices", result["content"])
        self.assertEqual(result["citations"][0]["source_path"], "governance.md")


if __name__ == "__main__":
    unittest.main()
