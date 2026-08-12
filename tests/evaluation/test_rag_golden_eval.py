import unittest
import json
from pathlib import Path

from evaluation.rag_golden_eval import bootstrap_mean_interval, citation_matches, evaluate_answer_governance, evaluate_retrieval_cases
from evaluation.rag_snapshot_retrievers import _tokens, build_bm25_retriever, build_dense_retriever, build_hybrid_rrf_retriever


class RagGoldenEvalTests(unittest.TestCase):
    def test_child_section_satisfies_stable_parent_citation(self):
        required = {"filename": "annual.pdf", "page": 5, "section": "年度报告"}
        actual = {"filename": "annual.pdf", "page": 5, "section": "年度报告 / 主要会计数据"}
        self.assertTrue(citation_matches(required, actual))
        self.assertFalse(citation_matches(actual, required))

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
        self.assertEqual(result["precision_at_3"], 0.3333)
        self.assertEqual(result["f1_at_3"], 0.5)
        self.assertEqual(result["mrr"], 0.5)
        self.assertEqual(result["citation_hit_rate"], 1.0)
        self.assertEqual(result["uncertainty"]["recall_at_3"]["cases"], 1)
        self.assertIn("unclassified", result["breakdown_by_source_type"])

    def test_bootstrap_interval_is_reproducible_and_contains_observed_mean(self):
        result = bootstrap_mean_interval([0.0, 1.0, 1.0], samples=100, seed=7)

        self.assertEqual(result, bootstrap_mean_interval([0.0, 1.0, 1.0], samples=100, seed=7))
        self.assertLessEqual(result["lower_95"], result["point_estimate"])
        self.assertGreaterEqual(result["upper_95"], result["point_estimate"])

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

    def test_bm25_tokenizer_keeps_chinese_terms_and_stock_codes(self):
        tokens = _tokens("贵州茅台 600519 营业收入")
        self.assertIn("600519", tokens)
        self.assertIn("营业", tokens)
        self.assertIn("收入", tokens)

    def test_retrieval_tracks_no_evidence_abstention_separately(self):
        cases = [{
            "id": "none", "corpus_version": "v1", "query": "dividend",
            "expected": {"relevant_evidence_ids": [], "required_citations": [], "abstain_allowed": True},
        }]
        result = evaluate_retrieval_cases(cases, lambda _query, *, top_k: [], k=3)
        self.assertEqual(result["abstain_retrieval_compliance_rate"], 1.0)

    def test_versioned_answer_fixture_passes_citation_and_abstention_contracts(self):
        root = Path(__file__).resolve().parents[2]
        answers = json.loads((root / "evaluation" / "fixtures" / "rag_answer_governance_v1.json").read_text(encoding="utf-8"))
        cases = [{
            "id": "fixture-cash-flow-001",
            "expected": {"required_citations": [{"filename": "fixture-annual-report-2025.md", "page": 32, "section": "Cash Flow Statement"}], "abstain_allowed": False},
        }, {
            "id": "fixture-revenue-002",
            "expected": {"required_citations": [{"filename": "fixture-annual-report-2025.md", "page": 18, "section": "Revenue"}], "abstain_allowed": False},
        }, {
            "id": "fixture-no-dividend-003",
            "expected": {"required_citations": [], "abstain_allowed": True},
        }]
        result = evaluate_answer_governance(cases, answers)
        self.assertEqual(result["citation_backlink_rate"], 1.0)
        self.assertEqual(result["abstain_compliance_rate"], 1.0)
        self.assertEqual(result["unsupported_answer_rate"], 0.0)
