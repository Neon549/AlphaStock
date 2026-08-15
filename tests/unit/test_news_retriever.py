import unittest
from unittest.mock import patch

from rag.retriever import (
    _is_official_announcement,
    _document_identity,
    _filter_live_news_by_entity,
    _rrf_ranked,
    _rerank_news_candidates,
    expand_finance_query,
    finance_query_facets,
    finance_title_matches,
    hybrid_retrieve_news,
)


class NewsRetrieverTests(unittest.TestCase):
    @patch("rag.retriever.get_stock_name", return_value="Example Corp")
    def test_live_entity_gate_drops_unrelated_sector_headlines(self, _get_stock_name):
        items = [
            "Example Corp launches a new product",
            "123456 stock buyback update",
            "Sector fund-flow list led by another company",
        ]

        self.assertEqual(
            _filter_live_news_by_entity("123456", items),
            ["Example Corp launches a new product", "123456 stock buyback update"],
        )

    def test_finance_query_expansion_keeps_original_and_adds_synonyms(self):
        expanded = expand_finance_query("600036 AI服务器需求")

        self.assertIn("600036 AI服务器需求", expanded)
        self.assertIn("人工智能服务器", expanded)
        self.assertIn("算力服务器", expanded)

    def test_weighted_rrf_can_prioritize_lexical_evidence(self):
        ranked = _rrf_ranked(
            ["semantic-only", "shared"],
            ["lexical-only", "shared"],
            vector_weight=1.0,
            bm25_weight=2.0,
        )

        order = [item for item, _ in ranked]
        self.assertLess(order.index("lexical-only"), order.index("semantic-only"))

    def test_announcement_chunks_share_one_document_identity(self):
        first = "公告：回购进展 内容：第一段 来源：巨潮资讯 链接：https://example.test/a.pdf"
        second = "公告：回购进展 内容：第二段 来源：巨潮资讯 链接：https://example.test/a.pdf"

        self.assertEqual(_document_identity(first), _document_identity(second))

    def test_live_and_persisted_news_with_same_title_are_deduplicated(self):
        live = "【2026-08-12】AI服务器需求增长"
        persisted = "【工业富联 | 2026-08-12】AI服务器需求增长"

        self.assertEqual(_document_identity(live), _document_identity(persisted))

    def test_official_announcement_is_identified_for_news_first_fallback(self):
        announcement = "公司（000001） 公告：关于股份回购的公告 内容：回购金额 来源：巨潮资讯 链接：https://example.test/a.pdf"

        self.assertTrue(_is_official_announcement(announcement))
        self.assertFalse(_is_official_announcement("【公司 | 2026-08-12】股份回购新闻"))

    def test_multi_intent_query_builds_separate_facets(self):
        facets = finance_query_facets("招商银行近期有什么人事变动或资金流动消息？")

        self.assertEqual(len(facets), 2)
        self.assertTrue(any("离任" in facet for facet in facets))
        self.assertTrue(any("主力资金" in facet for facet in facets))

    def test_business_facet_does_not_select_unrelated_announcement_body(self):
        facet = "新公司 子公司 注册资本 成立 业务扩展 合作 订单 中标"
        document = "公告：关于回购公司股份的进展公告 内容：公司设立过子公司"

        self.assertFalse(finance_title_matches(document, facet))
        self.assertTrue(finance_title_matches("汇川技术成立轨道交通设备公司", facet))


    def test_bge_cross_encoder_reranks_bounded_news_candidates(self):
        primary = [("weak lexical match", 4.0), ("strong semantic answer", 3.0)]
        with patch("rag.retriever._get_news_reranker", return_value=lambda _query, _passages: [0.1, 0.9]), patch(
            "rag.retriever.NEWS_RERANK_BGE_WEIGHT", 1.0
        ):
            selected, applied = _rerank_news_candidates("question", primary, primary, top_k=2)

        self.assertTrue(applied)
        self.assertEqual(selected, [("strong semantic answer", 0.9), ("weak lexical match", 0.1)])

    def test_bge_never_expands_the_lexical_evidence_set(self):
        lexical = [("first lexical evidence", 4.0)]
        primary = [*lexical, ("outside candidate", 3.0)]
        with patch("rag.retriever._get_news_reranker", return_value=lambda _query, _passages: [0.1]), patch(
            "rag.retriever.NEWS_RERANK_CANDIDATE_K", 1
        ):
            selected, applied = _rerank_news_candidates("question", primary, lexical, top_k=1)

        self.assertTrue(applied)
        self.assertEqual(selected, [("first lexical evidence", 0.1)])

    def test_blended_bge_reranker_preserves_a_stronger_bm25_match(self):
        primary = [("strong lexical match", 9.0), ("weak lexical semantic match", 1.0)]
        with patch("rag.retriever._get_news_reranker", return_value=lambda _query, _passages: [0.1, 0.9]), patch(
            "rag.retriever.NEWS_RERANK_BGE_WEIGHT", 0.5
        ):
            selected, applied = _rerank_news_candidates("question", primary, primary, top_k=2)

        self.assertTrue(applied)
        self.assertEqual(selected, [("strong lexical match", 0.1), ("weak lexical semantic match", 0.9)])

    def test_bge_non_finite_score_falls_back_to_bm25(self):
        primary = [("first lexical evidence", 4.0), ("second lexical evidence", 3.0)]
        with patch("rag.retriever._get_news_reranker", return_value=lambda _query, _passages: [float("nan"), 0.9]):
            selected, applied = _rerank_news_candidates("question", primary, primary, top_k=2)

        self.assertFalse(applied)
        self.assertEqual(selected, primary)

    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.retrieve_news_corpus")
    @patch("rag.retriever.get_stock_news")
    @patch("rag.retriever._get_news_reranker", return_value=None)
    def test_hybrid_retrieval_fuses_live_and_pgvector_candidates(self, _reranker, get_news, corpus, retrieve_news):
        get_news.invoke.return_value = "茅台业绩增长\n行业政策支持"
        corpus.return_value = ["【贵州茅台 | 2026-08-01】茅台业绩增长"]
        retrieve_news.return_value = "【贵州茅台 | 2026-08-01】茅台业绩增长"

        result = hybrid_retrieve_news("600519", "利润", top_k=3)

        self.assertIn("茅台业绩增长", result)
        kwargs = retrieve_news.call_args.kwargs
        self.assertEqual(kwargs["stock_code"], "600519")
        self.assertEqual(kwargs["k"], 20)
        self.assertEqual(kwargs["days"], 30)
        self.assertIn("利润", kwargs["query"])
        self.assertIn("归母净利润", kwargs["query"])

    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.retrieve_news_corpus")
    @patch("rag.retriever.get_stock_news")
    @patch("rag.retriever._get_news_reranker", return_value=None)
    def test_scoped_bm25_skips_embedding_when_top_k_is_satisfied(self, _reranker, get_news, corpus, retrieve_news):
        get_news.invoke.return_value = ""
        corpus.return_value = ["【工业富联 | 2026-08-12】AI服务器需求增长"]

        result = hybrid_retrieve_news("601138", "AI服务器需求", top_k=1)

        self.assertIn("AI服务器需求增长", result)
        retrieve_news.assert_not_called()

    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.retrieve_news_corpus")
    @patch("rag.retriever.get_stock_news")
    def test_hybrid_retrieval_returns_safe_empty_message_when_both_sources_fail(self, get_news, corpus, retrieve_news):
        get_news.invoke.return_value = "[TOOL_ERROR] source unavailable"
        corpus.return_value = []
        retrieve_news.return_value = "最近7天内未找到相关新闻"

        self.assertEqual(hybrid_retrieve_news("600519", "业绩"), "暂无可验证的相关新闻")

    @patch("control_plane.observability.record_rag_event")
    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.retrieve_news_corpus")
    @patch("rag.retriever.get_stock_news")
    @patch("rag.retriever._get_news_reranker", return_value=lambda _query, passages: [float(len(passages) - index) for index in range(len(passages))])
    def test_hybrid_retrieval_records_hashes_and_rrf_scores_only(self, _reranker, get_news, corpus, retrieve_news, record):
        get_news.invoke.return_value = "私有新闻标题"
        corpus.return_value = ["【贵州茅台 | 2026-08-01】业绩增长"]
        retrieve_news.return_value = "【贵州茅台 | 2026-08-01】业绩增长"

        hybrid_retrieve_news("600519", "业绩 联系电话 13800138000", top_k=2)

        retrieval_event = record.call_args_list[0].args[1]
        self.assertEqual(retrieval_event["query"]["query_preview"], "业绩 联系电话 [phone]")
        self.assertTrue(retrieval_event["rerank"]["applied"])
        self.assertEqual(retrieval_event["rerank"]["method"], "bge_cross_encoder_news")
        self.assertIn("news_sha256", retrieval_event["top_k"][0])
        self.assertNotIn("私有新闻标题", str(retrieval_event))
        self.assertEqual(record.call_args_list[1].args[1]["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
