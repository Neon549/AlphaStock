import unittest

from rag.news_indexer import EVAL_STOCKS, WATCH_LIST


class NewsIndexerConfigTests(unittest.TestCase):
    def test_evaluation_stocks_are_in_production_refresh_list(self):
        watch_codes = {code for code, _ in WATCH_LIST}

        self.assertTrue({code for code, _ in EVAL_STOCKS}.issubset(watch_codes))
        self.assertEqual(len(watch_codes), len(WATCH_LIST))


if __name__ == "__main__":
    unittest.main()
