import tempfile
import unittest
from pathlib import Path

from agent_runtime.memory.index import (
    MEMORY_KNOWLEDGE_DIR,
    _chunks,
    _file_chunks,
    _unique_source_results,
    approved_memory_files,
)


class MemoryIndexTests(unittest.TestCase):
    def test_only_human_approved_markdown_is_discoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved.md"
            approved.write_text("---\nstatus: approved\nscope: governance\n---\n# Evidence policy\nKeep source IDs.", encoding="utf-8")
            (root / "draft.md").write_text("---\nstatus: draft\n---\n# Draft\nNot indexable.", encoding="utf-8")

            self.assertEqual(approved_memory_files(root), [approved])
            chunks = _file_chunks(approved, root)
            self.assertEqual(chunks[0].source_path, "approved.md")
            self.assertEqual(chunks[0].metadata["scope"], "governance")
            self.assertTrue(chunks[0].evidence_id.startswith("memory:approved.md:"))

    def test_chunks_preserve_headings_and_bound_length(self):
        text = "# First\n" + ("A" * 750) + "\n# Second\nshort"
        chunks = _chunks(text)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertTrue(chunks[0].startswith("# First"))
        self.assertIn("# Second", chunks[-1])
        self.assertTrue(all(len(chunk) <= 600 for chunk in chunks))

    def test_bootstrap_corpus_covers_all_controlled_memory_scopes(self):
        scopes = {
            _file_chunks(path, MEMORY_KNOWLEDGE_DIR)[0].metadata["scope"]
            for path in approved_memory_files()
        }
        self.assertEqual(
            scopes,
            {"governance", "research", "retrieval", "workflow", "operations", "backtest", "evaluation"},
        )

    def test_search_result_keeps_one_nearest_chunk_per_source(self):
        rows = [
            ("a.md", "a" * 64, 0, "first", {}, 0.01),
            ("a.md", "a" * 64, 1, "duplicate", {}, 0.02),
            ("b.md", "b" * 64, 0, "second", {}, 0.03),
        ]

        results = _unique_source_results(rows, limit=3)
        self.assertEqual([item["source_path"] for item in results], ["a.md", "b.md"])
        self.assertEqual(results[0]["chunk_index"], 0)


if __name__ == "__main__":
    unittest.main()
