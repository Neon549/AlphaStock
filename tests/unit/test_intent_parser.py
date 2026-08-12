from __future__ import annotations

import unittest
from unittest.mock import patch

import api.intent_parser as parser


class _LLMResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str | Exception):
        self.content = content

    def invoke(self, _prompt: str):
        if isinstance(self.content, Exception):
            raise self.content
        return _LLMResponse(self.content)


class _FastTextModel:
    def predict(self, _query: str, k: int = 1):
        self.last_k = k
        return ["__label__analysis"], [0.97]


class IntentParserTests(unittest.TestCase):
    def test_discussion_hard_negative_is_routed_before_fasttext(self):
        result = parser.parse_intent("宁德时代的商业模式如何")

        self.assertEqual(result["intent"], 1)
        self.assertEqual(result["stock_code"], "300750")
        self.assertEqual(result["source"], "rule")

    def test_static_universe_completes_fasttext_name_slot_without_runtime_cache(self):
        model = _FastTextModel()
        with patch.object(parser, "_fasttext_model", model), patch.object(parser, "_fasttext_load_attempted", True):
            result = parser._fasttext_layer("请分析宁德时代")

        self.assertIsNotNone(result)
        self.assertEqual(result["intent"], 2)
        self.assertEqual(result["stock_code"], "300750")
        self.assertEqual(result["slot_sources"]["stock_code"], "static_universe")
        self.assertEqual(result["confidence"], 0.97)

    def test_explicit_user_code_wins_over_conflicting_model_slot(self):
        name, code, _, warnings = parser._resolve_stock_slots(
            "请分析 600519",
            candidate_name="宁德时代",
            candidate_code="300750",
        )

        self.assertEqual(code, "600519")
        self.assertEqual(name, "贵州茅台")
        self.assertIn("llm_stock_code_conflicts_with_explicit_query", warnings)

    def test_llm_code_is_rejected_when_not_backed_by_query_or_local_universe(self):
        fake_llm = _FakeLLM(
            '{"intent": 2, "stock_name": null, "stock_code": "399999", "analyst_focus": "technical"}'
        )
        with patch.object(parser, "_fasttext_layer", return_value=None), patch.object(parser.llm_cfg, "quick_llm", fake_llm):
            result = parser.parse_intent("帮我做个技术分析")

        self.assertEqual(result["intent"], 4)
        self.assertIsNone(result["stock_code"])
        self.assertIn("unverified_llm_stock_code_rejected", result["slot_warnings"])
        self.assertIn("analysis_requires_resolved_stock_code", result["slot_warnings"])

    def test_invalid_llm_slots_are_normalised_to_safe_clarification(self):
        fake_llm = _FakeLLM(
            '{"intent": 99, "stock_name": "宁德时代", "stock_code": "300750", "analyst_focus": "magic"}'
        )
        with patch.object(parser, "_fasttext_layer", return_value=None), patch.object(parser.llm_cfg, "quick_llm", fake_llm):
            result = parser.parse_intent("给我一个判断")

        self.assertEqual(result["intent"], 4)
        self.assertEqual(result["stock_code"], "300750")
        self.assertIn("invalid_llm_intent", result["slot_warnings"])
        self.assertIn("invalid_llm_analyst_focus", result["slot_warnings"])

    def test_multi_focus_request_is_preserved_as_all(self):
        result = parser.parse_intent("600519 的技术面和财务基本面一起看")

        self.assertEqual(result["intent"], 2)
        self.assertEqual(result["analyst_focus"], "all")
        self.assertEqual(result["slot_sources"]["analyst_focus"], "rule_keywords")

    def test_llm_parse_failure_fails_closed_to_clarification(self):
        fake_llm = _FakeLLM(RuntimeError("network unavailable"))
        with patch.object(parser, "_fasttext_layer", return_value=None), patch.object(parser.llm_cfg, "quick_llm", fake_llm):
            result = parser.parse_intent("我想做个分析")

        self.assertEqual(result["intent"], 4)
        self.assertEqual(result["source"], "llm_fallback")
        self.assertIn("llm_parse_failed", result["slot_warnings"])

    def test_compound_request_becomes_sequenced_sub_intents(self):
        result = parser.parse_intent("先分析宁德时代的基本面，然后回测均线策略")
        tasks = {task["intent"]: task for task in result["sub_intents"]}

        self.assertEqual(result["intent"], 2)
        self.assertTrue(result["multi_intent"])
        self.assertEqual(tasks["investment_analysis"]["slots"]["stock_code"], "300750")
        self.assertEqual(tasks["investment_analysis"]["slots"]["analyst_focus"], "fundamental")
        self.assertEqual(tasks["backtest"]["depends_on"], ["analysis-1"])

    def test_compound_request_without_sequence_can_be_parallel(self):
        result = parser.parse_intent("分析宁德时代并回测均线策略")
        tasks = {task["intent"]: task for task in result["sub_intents"]}

        self.assertEqual(tasks["investment_analysis"]["depends_on"], [])
        self.assertEqual(tasks["backtest"]["depends_on"], [])

    def test_trade_action_is_a_confirmation_task_not_a_tool_request(self):
        result = parser.parse_intent("先分析宁德时代，价格合适就帮我下单")
        tasks = {task["intent"]: task for task in result["sub_intents"]}

        self.assertEqual(result["intent"], 2)
        self.assertTrue(tasks["trade_action"]["requires_confirmation"])
        self.assertEqual(tasks["trade_action"]["depends_on"], ["analysis-1"])


if __name__ == "__main__":
    unittest.main()
