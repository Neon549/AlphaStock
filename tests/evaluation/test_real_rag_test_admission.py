from evaluation.real_rag_test_admission import audit_final_test_rows


def _row(*, query: str = "宁德时代 2026 年一季度收入？", filename: str = "300750-2026-q1.pdf", origin: str = "deidentified_session") -> dict:
    return {
        "id": "real-1",
        "split": "test",
        "source_type": "quarterly_report",
        "corpus_version": "sha256:untouched-corpus",
        "query": query,
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "100.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["300750-2026-q1:p8:c0"],
            "required_citations": [{"filename": filename, "page": 8, "section": "主要会计数据"}],
            "abstain_allowed": False,
        },
        "provenance": {"origin": origin, "reviewer": "reviewer-b", "reviewed_at": "2026-08-12"},
    }


def _reference() -> dict:
    return _row(query="宁德时代 2025 年收入？", filename="300750-2025-annual.pdf")


def test_final_test_accepts_deidentified_unseen_case() -> None:
    report = audit_final_test_rows([_row()], [_reference()])

    assert report["valid"] is True


def test_final_test_rejects_document_overlap_even_when_query_differs() -> None:
    candidate = _row(filename="300750-2025-annual.pdf")
    report = audit_final_test_rows([candidate], [_reference()])

    assert report["valid"] is False
    assert "source document overlaps" in " ".join(report["errors"])


def test_final_test_rejects_identity_data_and_non_real_origin() -> None:
    candidate = _row(origin="manual")
    candidate["session_id"] = "session-123"
    candidate["query"] = "请发到 yulin@example.com"
    report = audit_final_test_rows([candidate], [_reference()])

    assert report["valid"] is False
    messages = " ".join(report["errors"])
    assert "origin must be one of" in messages
    assert "forbidden identity field session_id" in messages
    assert "possible email" in messages


def test_financial_answer_values_are_not_scanned_as_phone_numbers() -> None:
    candidate = _row()
    candidate["expected"]["answer_facts"][0]["value"] = "13912345678.90"
    report = audit_final_test_rows([candidate], [_reference()])

    assert report["valid"] is True
