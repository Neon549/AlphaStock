from evaluation.build_financebench_chunks import build_chunks


def test_chunks_keep_the_original_page_as_the_evaluation_backlink() -> None:
    chunks = build_chunks([{
        "evidence_id": "ACME:p7:c0", "document_id": "ACME", "page": 7,
        "text": "a" * 16,
    }], size=10, overlap=2)

    assert [chunk["evidence_id"] for chunk in chunks] == ["ACME:p7:c0:c0", "ACME:p7:c0:c1"]
    assert all(chunk["page_evidence_id"] == "ACME:p7:c0" for chunk in chunks)
    assert chunks[1]["text"].startswith("a" * 2)
