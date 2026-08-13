import unittest
from unittest.mock import MagicMock, patch
from argparse import Namespace

from rag.news_indexer import (
    EVAL_STOCKS,
    WATCH_LIST,
    _build_news_item,
    _insert_news_batch,
    _news_id,
    news_evidence_snippet,
)
from scripts.refresh_news_index import _stock_list


class NewsIndexerConfigTests(unittest.TestCase):
    def test_evaluation_stocks_are_in_production_refresh_list(self):
        watch_codes = {code for code, _ in WATCH_LIST}

        self.assertTrue({code for code, _ in EVAL_STOCKS}.issubset(watch_codes))
        self.assertEqual(len(watch_codes), len(WATCH_LIST))

    @patch("rag.news_indexer._embed")
    @patch("rag.news_indexer.get_conn")
    def test_existing_news_is_skipped_before_embedding(self, get_conn, embed):
        item = {
            "stock_code": "601138",
            "stock_name": "工业富联",
            "title": "AI 算力需求增长",
            "full_text": "工业富联 AI 算力需求增长",
            "pub_time": "2026-08-12 08:00:00",
            "date": "2026-08-12",
        }
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            (_news_id(item["stock_code"], item["title"]), len(item["full_text"]) + 10)
        ]
        get_conn.return_value.__enter__.return_value = connection

        self.assertEqual(_insert_news_batch([item]), 0)
        embed.assert_not_called()

    def test_refresh_cli_normalizes_numeric_stock_codes(self):
        stocks = _stock_list(Namespace(stocks="601138,2415", all_existing=False, port=15432))

        self.assertEqual(stocks, [("601138", "工业富联"), ("002415", "海康威视")])

    def test_news_item_includes_body_source_and_link(self):
        item = _build_news_item(
            {
                "发布时间": "2026-08-12 08:00:00",
                "新闻标题": "工业富联半年报",
                "新闻内容": "AI算力需求持续增长，归母净利润237.40亿元。",
                "文章来源": "测试来源",
                "新闻链接": "https://example.test/news",
            },
            "601138",
            "工业富联",
        )

        self.assertIn("AI算力需求持续增长", item["full_text"])
        self.assertIn("来源：测试来源", item["full_text"])
        self.assertIn("链接：https://example.test/news", item["full_text"])

    @patch("rag.news_indexer.execute_values", return_value=[("id",)])
    @patch("rag.news_indexer._embed", return_value=[[0.1, 0.2]])
    @patch("rag.news_indexer.get_conn")
    def test_duplicate_titles_are_deduplicated_before_upsert(self, get_conn, embed, execute_values):
        short = {
            "stock_code": "000002",
            "stock_name": "万科A",
            "title": "同一标题",
            "full_text": "短文本",
            "pub_time": "2026-08-12 08:00:00",
            "date": "2026-08-12",
        }
        long = {**short, "full_text": "这是信息更完整的长文本"}
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        get_conn.return_value.__enter__.return_value = connection

        stats = _insert_news_batch([short, long], return_stats=True)

        embed.assert_called_once_with([long["full_text"]])
        self.assertEqual(stats, {"added": 1, "updated": 0, "embedded": 1})

    def test_news_evidence_snippet_selects_query_relevant_sentence(self):
        full_text = (
            "工业富联（601138） 标题：半年报发布 "
            "内容：公司召开例行会议。AI算力需求持续爆发式增长。办公楼完成装修。 "
            "来源：中国基金报 链接：https://example.test/news"
        )

        snippet = news_evidence_snippet("半年报发布", full_text, "AI服务器需求")

        self.assertIn("AI算力需求持续爆发式增长", snippet)
        self.assertNotIn("办公楼完成装修", snippet)
        self.assertIn("来源：中国基金报", snippet)


if __name__ == "__main__":
    unittest.main()
