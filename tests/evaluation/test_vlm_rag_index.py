import unittest
from unittest.mock import patch

from api import multimodal


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeConnection:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class VlmRagIndexTests(unittest.TestCase):
    def test_successful_vlm_result_is_written_as_session_scoped_evidence(self):
        connection = _FakeConnection()
        with patch.object(
            multimodal, "_embed", side_effect=lambda texts: [[0.1] * 768 for _ in texts]
        ) as embed, patch.object(
            multimodal, "get_conn", return_value=connection
        ):
            result = multimodal.index_image_analysis(
                b"image-bytes",
                "report.png",
                "session-a",
                {"data_type": "financial", "extracted_data": "净利润：10亿元"},
                "这张财报截图的净利润是多少？",
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["indexed"])
        self.assertGreaterEqual(result["chunk_count"], 1)
        self.assertTrue(connection.committed)
        embed.assert_called_once()
        delete_call, *insert_calls = connection.cursor_obj.calls
        self.assertIn("DELETE FROM uploaded_document_chunks", delete_call[0])
        self.assertEqual(delete_call[1][0], "session-a")
        self.assertEqual(len(insert_calls), result["chunk_count"])
        insert_call = insert_calls[-1]
        self.assertIn("INSERT INTO uploaded_document_chunks", insert_call[0])
        self.assertEqual(insert_call[1][1], "session-a")
        self.assertEqual(insert_call[1][2], "report.png")
        self.assertIn("图像识别结果", insert_call[1][10])
        self.assertIn("VLM 提取事实", insert_call[1][10])

    def test_unknown_or_empty_vlm_result_is_not_indexed(self):
        result = multimodal.index_image_analysis(
            b"image-bytes", "report.png", "session-a", {"data_type": "unknown", "extracted_data": ""}
        )
        self.assertFalse(result["indexed"])


if __name__ == "__main__":
    unittest.main()
