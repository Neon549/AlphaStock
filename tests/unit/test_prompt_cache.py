"""不调用模型 API 的 Prompt Cache 组装单测。"""

import unittest

from config.prompt_cache import build_cacheable_messages


class PromptCacheMessageTests(unittest.TestCase):
    def test_stable_prefix_is_a_system_message(self):
        messages = build_cacheable_messages("固定规则 v1", "股票代码：600519")
        self.assertEqual(messages[0].type, "system")
        self.assertEqual(messages[0].content, "固定规则 v1")
        self.assertEqual(messages[1].type, "human")
        self.assertEqual(messages[1].content, "股票代码：600519")

    def test_empty_sections_are_rejected(self):
        with self.assertRaises(ValueError):
            build_cacheable_messages("", "动态任务")
        with self.assertRaises(ValueError):
            build_cacheable_messages("固定规则", "")


if __name__ == "__main__":
    unittest.main()
