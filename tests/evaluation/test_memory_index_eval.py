import unittest

from evaluation.memory_index_eval import evaluate_cases


class MemoryIndexEvalTests(unittest.TestCase):
    def test_recall_and_mrr_use_source_path_gold_labels(self):
        cases = [{"id": "a", "query": "q", "relevant_source_paths": ["good.md"]}]
        def searcher(_query, *, top_k):
            return [{"source_path": "other.md"}, {"source_path": "good.md"}]
        result = evaluate_cases(cases, searcher, k=3)
        self.assertEqual(result["recall_at_3"], 1.0)
        self.assertEqual(result["mrr"], 0.5)

    def test_quality_metrics_track_forbidden_and_non_operational_memory(self):
        cases = [{
            "id": "a", "query": "q", "scope": "governance",
            "relevant_source_paths": ["good.md"], "forbidden_source_paths": ["bad.md"],
        }]

        def searcher(_query, *, top_k):
            return [
                {"source_path": "good.md", "metadata": {"evidence_class": "operating_knowledge"}},
                {"source_path": "bad.md", "metadata": {"evidence_class": "market_evidence"}},
            ]

        result = evaluate_cases(cases, searcher, k=3)
        self.assertEqual(result["precision_at_3"], 0.5)
        self.assertEqual(result["forbidden_recall_rate"], 1.0)
        self.assertEqual(result["evidence_class_violation_rate"], 1.0)
