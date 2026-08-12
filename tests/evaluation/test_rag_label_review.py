from evaluation.build_rag_label_review import build_review_queue


def test_review_queue_keeps_alternative_evidence_pending() -> None:
    cases = [{
        "id": "case-1",
        "query": "2025 年营业收入是多少？",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "123456.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p1:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 1, "section": "年度报告"}],
        },
    }]
    corpus = [
        {"evidence_id": "doc:p1:c0", "document_id": "doc", "page": 1, "section": "年度报告", "content": "营业收入 123,456.00"},
        {"evidence_id": "doc:p2:c0", "document_id": "doc", "page": 2, "section": "财务报表", "content": "2025 年营业收入 123,456.00"},
    ]
    ablation = {"results": {"bm25": {
        "recall_at_10": 0.0,
        "mrr": 0.0,
        "ndcg_at_10": 0.0,
        "citation_hit_rate": 0.0,
        "abstain_retrieval_compliance_rate": None,
        "details": [{"id": "case-1", "result_ids": ["doc:p2:c0"]}],
    }}}

    result = build_review_queue(cases, corpus, ablation)

    assert result["cases_for_review"] == 1
    assert result["items"][0]["review_decision"] == "pending_human_review"
    assert result["items"][0]["retrieved_support_by_method"]["bm25"] == ["doc:p2:c0"]
    assert len(result["items"][0]["candidate_supporting_evidence"]) == 2
    assert result["metric_summary"]["bm25"]["candidate_diagnostics"]["fact_value_hit_at_10"] == 1.0


def test_review_queue_surfaces_reference_answer_page_mismatch() -> None:
    cases = [{
        "id": "cfqa-1",
        "query": "2020 年库存股余额是多少？",
        "reference_answer": "库存股余额为 28,263,248.47 元。",
        "expected": {
            "answer_facts": [],
            "relevant_evidence_ids": ["doc:p175:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 175, "section": "专项储备"}],
        },
    }]
    corpus = [
        {"evidence_id": "doc:p175:c0", "document_id": "doc", "page": 175, "section": "专项储备", "content": "专项储备 不适用"},
        {"evidence_id": "doc:p110:c0", "document_id": "doc", "page": 110, "section": "资产负债表", "content": "减：库存股 28,263,248.47"},
    ]
    ablation = {"results": {"bm25": {
        "recall_at_10": 0.0,
        "mrr": 0.0,
        "ndcg_at_10": 0.0,
        "citation_hit_rate": 0.0,
        "abstain_retrieval_compliance_rate": None,
        "details": [{"id": "cfqa-1", "result_ids": ["doc:p110:c0"]}],
    }}}

    result = build_review_queue(cases, corpus, ablation)

    item = result["items"][0]
    assert item["review_decision"] == "pending_human_review"
    assert item["candidate_supporting_evidence"][0]["evidence_id"] == "doc:p110:c0"
    assert "28,263,248.47" in item["candidate_supporting_evidence"][0]["matched_terms"]
    assert item["retrieved_support_by_method"]["bm25"] == ["doc:p110:c0"]
