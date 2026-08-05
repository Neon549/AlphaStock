import unittest

from evaluation.rag_golden_eval import evaluate_answer_governance, evaluate_retrieval_cases
from evaluation.rag_snapshot_retrievers import build_bm25_retriever, build_dense_retriever, build_hybrid_rrf_retriever


class RagGoldenEvalTests(unittest.TestCase):
    def setUp(self):
        self.cases = [{
            "id": "cash", "corpus_version": "v1", "query": "cash flow",
            "expected": {
                "relevant_evidence_ids": ["evidence:p32"],
                "required_citations": [{"filename": "annual.md", "page": 32, "section": "Cash Flow"}],
                "abstain_allowed": False,
            },
        }]

    def test_retrieval_metrics_use_evidence_id_and_page_backlink(self):
        def retriever(_query, *, top_k):
            return [{"evidence_id": "wrong"}, {"evidence_id": "evidence:p32", "filename": "annual.md", "page": 32, "section": "Cash Flow"}]

        result = evaluate_retrieval_cases(self.cases, retriever, k=3)
        self.assertEqual(result["recall_at_3"], 1.0)
        self.assertEqual(result["mrr"], 0.5)
        self.assertEqual(result["citation_hit_rate"], 1.0)

    def test_answer_governance_detects_missing_citation_and_unsupported_claim(self):
        result = evaluate_answer_governance(self.cases, {
            "cash": {"citations": [], "unsupported_claims": ["invented margin"]},
        })
        self.assertEqual(result["citation_backlink_rate"], 0.0)
        self.assertEqual(result["unsupported_answer_rate"], 1.0)

    def test_snapshot_adapters_return_original_evidence_records(self):
        corpus = [
            {"evidence_id": "cash", "filename": "annual.md", "page": 32, "section": "Cash", "content": "operating cash flow 12.30"},
            {"evidence_id": "revenue", "filename": "annual.md", "page": 18, "section": "Revenue", "content": "revenue 20.50"},
        ]
        embedding = lambda texts: [[1.0, 0.0] if "cash" in text else [0.0, 1.0] for text in texts]
        bm25 = build_bm25_retriever(corpus)
        dense = build_dense_retriever(corpus, embedding)
        hybrid = build_hybrid_rrf_retriever(corpus, bm25, dense)

        self.assertEqual(bm25("cash flow", top_k=1)[0]["evidence_id"], "cash")
        self.assertEqual(dense("cash flow", top_k=1)[0]["evidence_id"], "cash")
        self.assertEqual(hybrid("cash flow", top_k=1)[0]["page"], 32)
