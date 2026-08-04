import json
import unittest

from agent_runtime.memory.maintenance import extract_candidate_drafts


class _Response:
    def __init__(self, content):
        self.content = content


class _WorkerLlm:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, prompt):
        self.prompt = prompt
        return _Response(json.dumps(self.payload))


class MemoryMaintenanceTests(unittest.TestCase):
    def test_extractor_keeps_only_taxonomy_bound_candidate_drafts(self):
        llm = _WorkerLlm({"candidates": [
            {"title": "Evidence fallback", "category": "governance", "content": "When a source fails, state the gap and request review before proceeding."},
            {"title": "Bad category", "category": "market", "content": "This must never become an operating-memory lesson."},
        ]})
        drafts = extract_candidate_drafts([
            {"id": 1, "role": "user", "content": "tool failed"},
            {"id": 2, "role": "assistant", "content": "request review"},
        ], llm)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["category"], "governance")
        self.assertIn("Transcript", llm.prompt)

    def test_extractor_fails_closed_when_worker_does_not_return_json(self):
        class InvalidLlm:
            def invoke(self, _prompt):
                return _Response("not structured")

        self.assertEqual(extract_candidate_drafts([{"id": 1, "role": "user", "content": "x"}], InvalidLlm()), [])


if __name__ == "__main__":
    unittest.main()
