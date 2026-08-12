import unittest
from unittest.mock import patch

from rag.retriever import hybrid_retrieve_news


class NewsRetrieverTests(unittest.TestCase):
    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.get_stock_news")
    def test_hybrid_retrieval_fuses_live_and_pgvector_candidates(self, get_news, retrieve_news):
        get_news.invoke.return_value = "茅台业绩增长\n行业政策支持"
        retrieve_news.return_value = "【贵州茅台 | 2026-08-01】茅台业绩增长"

        result = hybrid_retrieve_news("600519", "业绩", top_k=3)

        self.assertIn("茅台业绩增长", result)
        retrieve_news.assert_called_once_with(query="业绩", stock_code="600519", k=6, days=7)

    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.get_stock_news")
    def test_hybrid_retrieval_returns_safe_empty_message_when_both_sources_fail(self, get_news, retrieve_news):
        get_news.invoke.return_value = "[TOOL_ERROR] source unavailable"
        retrieve_news.return_value = "最近7天内未找到相关新闻"

        self.assertEqual(hybrid_retrieve_news("600519", "业绩"), "暂无可验证的相关新闻")

    @patch("control_plane.observability.record_rag_event")
    @patch("rag.retriever.retrieve_news")
    @patch("rag.retriever.get_stock_news")
    def test_hybrid_retrieval_records_hashes_and_rrf_scores_only(self, get_news, retrieve_news, record):
        get_news.invoke.return_value = "私有新闻标题"
        retrieve_news.return_value = "【贵州茅台 | 2026-08-01】业绩增长"

        hybrid_retrieve_news("600519", "联系电话 13800138000", top_k=2)

        retrieval_event = record.call_args_list[0].args[1]
        self.assertEqual(retrieval_event["query"]["query_preview"], "联系电话 [phone]")
        self.assertTrue(retrieval_event["rerank"]["applied"])
        self.assertIn("news_sha256", retrieval_event["top_k"][0])
        self.assertNotIn("私有新闻标题", str(retrieval_event))
        self.assertEqual(record.call_args_list[1].args[1]["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
