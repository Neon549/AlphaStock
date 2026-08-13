import unittest
from unittest.mock import MagicMock, patch
from argparse import Namespace

from rag.news_indexer import EVAL_STOCKS, WATCH_LIST, _insert_news_batch, _news_id
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
        cursor.fetchall.return_value = [(_news_id(item["stock_code"], item["title"]),)]
        get_conn.return_value.__enter__.return_value = connection

        self.assertEqual(_insert_news_batch([item]), 0)
        embed.assert_not_called()

    def test_refresh_cli_normalizes_numeric_stock_codes(self):
        stocks = _stock_list(Namespace(stocks="601138,2415", all_existing=False, port=15432))

        self.assertEqual(stocks, [("601138", "工业富联"), ("002415", "海康威视")])


if __name__ == "__main__":
    unittest.main()
