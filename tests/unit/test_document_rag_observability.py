import unittest
from unittest.mock import patch

from agent_runtime.skills.document_rag.handler import run


class DocumentRagObservabilityTests(unittest.TestCase):
    def test_records_retrieval_and_structural_citation_validation(self):
        retrieval = {
            "status": "ok",
            "retrieved_evidence_ids": ["chunk-1", "chunk-2"],
            "retrieved_chunk_count": 2,
            "top_k": [{"evidence_id": "chunk-1", "distance": 0.12}],
            "corpus_snapshot": {"source_kind": "session_upload", "document_count": 1, "documents": []},
            "rerank": {"applied": False, "reason": "not_configured"},
        }
        citations = [{"evidence_id": "chunk-1", "page": 3}]
        with (
            patch("agent_runtime.skills.document_rag.handler.retrieve_document_evidence", return_value=("context", citations, retrieval)),
            patch("agent_runtime.skills.document_rag.handler.record_rag_event") as record,
        ):
            result = run(session_id="session-a", query="联系电话 13800138000")

        self.assertEqual(result["citations"], citations)
        self.assertEqual(record.call_count, 2)
        retrieval_event = record.call_args_list[0].args[1]
        self.assertEqual(retrieval_event["query"]["query_preview"], "联系电话 [phone]")
        validation_event = record.call_args_list[1].args[1]
        self.assertEqual(validation_event["status"], "passed")
        self.assertEqual(validation_event["citation_count"], 1)

    def test_empty_retrieval_is_marked_as_not_applicable_for_citations(self):
        retrieval = {"status": "abstained", "retrieved_evidence_ids": []}
        with (
            patch("agent_runtime.skills.document_rag.handler.retrieve_document_evidence", return_value=("", [], retrieval)),
            patch("agent_runtime.skills.document_rag.handler.record_rag_event") as record,
        ):
            run(session_id="session-a", query="无结果")

        self.assertEqual(record.call_args_list[1].args[1]["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
