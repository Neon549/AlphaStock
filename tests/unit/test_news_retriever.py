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


if __name__ == "__main__":
    unittest.main()
