import unittest

from agent_runtime.memory.candidates import MemoryCandidate, _validate, render_approved_markdown


class MemoryCandidateTests(unittest.TestCase):
    def test_candidate_requires_reviewable_content_and_safe_category(self):
        candidate = MemoryCandidate("id-1", "Evidence boundary", "A" * 30, "governance")
        _validate(candidate)
        with self.assertRaises(ValueError):
            _validate(MemoryCandidate("id-2", "x", "short", "governance"))
        with self.assertRaises(ValueError):
            _validate(MemoryCandidate("id-3", "x", "A" * 30, "../unsafe"))
        with self.assertRaises(ValueError):
            _validate(MemoryCandidate("id-4", "x", "A" * 30, "market"))
        with self.assertRaises(ValueError):
            _validate(MemoryCandidate("id-5", "x", "Guaranteed return for this stock", "governance"))

    def test_approved_markdown_is_attributed_and_indexable(self):
        text = render_approved_markdown({
            "candidate_id": "abc", "category": "governance", "title": "Evidence boundary",
            "content": "Keep current evidence separate from reusable process guidance.",
        }, "yulin")
        self.assertIn("status: approved", text)
        self.assertIn("source_candidate: abc", text)
        self.assertIn("market_fact_policy: never_override_current_evidence", text)
        self.assertIn("# Evidence boundary", text)


if __name__ == "__main__":
    unittest.main()
