"""Unit coverage for the hierarchy-preserving document splitter."""

import unittest

from api.document_processing.chunking import build_hierarchical_chunks, parse_heading
from api.document_processing.parsers import parse_document_pages
from api.document_processing.retrieval import extract_document_citations


class DocumentChunkingTests(unittest.TestCase):
    def test_parse_common_heading_formats(self):
        self.assertEqual(parse_heading("## 经营情况"), (2, "经营情况"))
        self.assertEqual(parse_heading("第二章 财务分析"), (1, "第二章 财务分析"))
        self.assertEqual(parse_heading("1.2 盈利能力"), (2, "1.2 盈利能力"))

    def test_children_keep_section_path_and_page(self):
        chunks = build_hierarchical_chunks(
            [(7, "# 年度报告\n概述内容。\n## 经营情况\n经营现金流持续改善。")]
        )

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["page"], 7)
        self.assertEqual(chunks[0]["parent_path"], "年度报告")
        self.assertEqual(chunks[1]["parent_path"], "年度报告 > 经营情况")
        self.assertIn("【章节】年度报告 > 经营情况", chunks[1]["text"])

    def test_plain_text_parser_preserves_content_without_page_claims(self):
        pages, parser = parse_document_pages("研发投入增长".encode("utf-8"), "note.txt")

        self.assertEqual(parser, "plain-text")
        self.assertEqual(pages, [(0, "研发投入增长")])

    def test_evidence_headers_are_converted_to_citations(self):
        citations = extract_document_citations(
            "[命中子块 | evidence_id=doc_a_1 | 文件=年报.pdf | 章节=经营情况 | 第 12 页 | 版本=v1]\n现金流改善"
        )

        self.assertEqual(citations[0]["evidence_id"], "doc_a_1")
        self.assertEqual(citations[0]["filename"], "年报.pdf")
        self.assertEqual(citations[0]["section"], "经营情况")
        self.assertEqual(citations[0]["page"], 12)
