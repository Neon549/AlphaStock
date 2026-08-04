import unittest

from agent_runtime.context.window import ContextWindowBuilder


class ContextWindowTests(unittest.TestCase):
    def test_research_window_composes_bootstrap_memory_skills_and_current_request(self):
        window = ContextWindowBuilder().build(
            profile="research",
            user_message="分析 600519，重点看财报",
            selected_skill_summaries=["document-rag@1.0.0+abc"],
            memory_context={
                "session": {"last_stock_code": "600519"},
                "preferences": {"answer_style": "concise"},
                "recent_transcript": [{"role": "user", "content": "继续上次的茅台分析"}],
            },
        )
        self.assertIn("Investment runtime boundary", window.text)
        self.assertIn("document-rag@1.0.0+abc", window.text)
        self.assertIn("分析 600519", window.text)
        self.assertIn("继续上次的茅台分析", window.text)
        self.assertEqual(window.mode, "normal")
        self.assertEqual(window.soft_limit, 4_200)
        self.assertEqual(window.hard_limit, 5_100)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            ContextWindowBuilder().build(
                profile="analyst", user_message="x", memory_context={}
            )

    def test_oversized_current_request_is_blocked_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "safe prompt budget"):
            ContextWindowBuilder().build(
                profile="discussion", user_message="测" * 5_000, memory_context={}
            )


if __name__ == "__main__":
    unittest.main()
