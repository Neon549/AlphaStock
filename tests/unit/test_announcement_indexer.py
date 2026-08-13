import unittest

from rag.announcement_indexer import (
    announcement_priority,
    build_announcement_items,
    select_evidence_chunks,
)


class AnnouncementIndexerTests(unittest.TestCase):
    def test_high_value_titles_are_selected_and_legal_opinions_are_rejected(self):
        self.assertGreater(announcement_priority("2026年半年度权益分派实施公告"), 0)
        self.assertGreater(announcement_priority("关于董事会秘书离任的公告"), 0)
        self.assertEqual(announcement_priority("关于股东大会的法律意见书"), 0)

    def test_signal_chunks_are_retained(self):
        text = "普通介绍。" * 500 + "营业收入增长，归母净利润92亿元，同比增长98%。" + "尾部。" * 500

        chunks = select_evidence_chunks(text, chunk_chars=200, overlap_chars=20, max_chunks=3)

        self.assertEqual(len(chunks), 3)
        self.assertTrue(any("净利润92亿元" in chunk for chunk in chunks))

    def test_title_only_fallback_keeps_provenance(self):
        metadata = {
            "announcement_id": "1225000000",
            "stock_code": "002415",
            "stock_name": "海康威视",
            "title": "2026年半年度权益分派实施公告",
            "pub_time": "2026-08-12",
            "date": "2026-08-12",
            "source_url": "https://static.cninfo.com.cn/example.pdf",
        }

        items = build_announcement_items(metadata, b"")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source_kind"], "announcement")
        self.assertEqual(items[0]["publisher"], "巨潮资讯")
        self.assertIn("权益分派实施公告", items[0]["full_text"])


if __name__ == "__main__":
    unittest.main()
