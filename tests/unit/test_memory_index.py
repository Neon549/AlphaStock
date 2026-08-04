import tempfile
import unittest
from pathlib import Path

from agent_runtime.memory.index import _chunks, _file_chunks, approved_memory_files


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


if __name__ == "__main__":
    unittest.main()
