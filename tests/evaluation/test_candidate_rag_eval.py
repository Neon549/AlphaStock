from evaluation.frozen_dataset import load_jsonl, validate_rag_rows
from evaluation.run_candidate_rag_eval import (
    DEFAULT_CASES,
    _scoped_corpus,
    _expanded_query,
    add_fact_support_diagnostics,
    build_scoped_retriever_bundle,
    load_candidate_corpus,
    validate_label_integrity,
)


def test_candidate_rag_cases_are_structurally_valid_but_not_reviewed_production_data() -> None:
    rows = load_jsonl(DEFAULT_CASES)

    smoke_report = validate_rag_rows(rows, require_reviewed_provenance=False)
    production_report = validate_rag_rows(rows, require_reviewed_provenance=True)
    assert len(rows) == 22
    assert smoke_report["valid"] is True
    assert production_report["valid"] is False
    assert "missing provenance.reviewed_at" in " ".join(production_report["errors"])


def test_scope_filters_company_period_and_rejects_missing_quarter() -> None:
    corpus = [
        {"company": "平安银行", "security_code": "000001", "report_period": "FY2024"},
        {"company": "平安银行", "security_code": "000001", "report_period": "FY2025"},
        {"company": "贵州茅台", "security_code": "600519", "report_period": "FY2024"},
        {"company": "天齐锂业", "security_code": "002466", "report_period": "2025H1"},
    ]

    assert _scoped_corpus("平安银行 2024 年营业收入", corpus) == [corpus[0]]
    assert _scoped_corpus("平安银行 2026 年第一季度营业收入", corpus) == []
    assert _scoped_corpus("天齐锂业 2025 年第三季度营业收入", corpus) == []
    assert _scoped_corpus("天齐锂业 2025 年半年度营业收入", corpus) == [corpus[3]]


def test_query_expansion_only_adds_deterministic_financial_aliases() -> None:
    expanded = _expanded_query("天齐锂业归母净利润和经营现金流怎么样")

    assert "天齐锂业归母净利润和经营现金流怎么样" in expanded
    assert "归属于上市公司股东的净利润" in expanded
    assert "经营活动产生的现金流量净额" in expanded


def test_heldout_curve_summary_keeps_only_headline_metrics() -> None:
    from evaluation.run_heldout_rag_eval import _metric_summary

    summary = _metric_summary({
        "k": 3,
        "results": {"bm25": {"hit_rate_at_3": 1.0, "recall_at_3": 0.8, "precision_at_3": 0.2, "f1_at_3": 0.32, "mrr": 0.7, "ndcg_at_3": 0.6, "citation_hit_rate": 1.0, "abstain_retrieval_compliance_rate": 1.0, "details": ["omitted"]}},
    })

    assert summary == {"bm25": {"hit_rate_at_3": 1.0, "recall_at_3": 0.8, "precision_at_3": 0.2, "f1_at_3": 0.32, "mrr": 0.7, "ndcg_at_3": 0.6, "citation_hit_rate": 1.0, "abstain_retrieval_compliance_rate": 1.0}}


def test_loaded_chunks_inherit_security_code_and_report_period(tmp_path) -> None:
    chunks = tmp_path / "chunks.jsonl"
    manifest = tmp_path / "sources.json"
    chunks.write_text('{"evidence_id":"doc:p1:c0","document_id":"doc","page":1,"parent_path":[],"text":"收入"}\n', encoding="utf-8")
    manifest.write_text(
        '{"documents":[{"document_id":"doc","company":"测试公司","security_code":"000777","report_period":"2026Q1","title":"报告","source_url":"https://example.test/doc.pdf","source_host":"example.test","published_at":"2026-04-30"}]}',
        encoding="utf-8",
    )

    corpus = load_candidate_corpus(chunks, manifest)

    assert corpus[0]["security_code"] == "000777"
    assert corpus[0]["report_period"] == "2026Q1"


def test_run_can_use_a_non_default_source_manifest(tmp_path) -> None:
    from evaluation.run_candidate_rag_eval import run

    chunks = tmp_path / "chunks.jsonl"
    manifest = tmp_path / "sources.json"
    cases = tmp_path / "cases.jsonl"
    chunks.write_text('{"evidence_id":"doc:p1:c0","document_id":"doc","page":1,"parent_path":["报告"],"text":"营业收入 123 元"}\n', encoding="utf-8")
    manifest.write_text(
        '{"documents":[{"document_id":"doc","company":"测试公司","security_code":"000777","report_period":"FY2025","title":"报告","source_url":"https://example.test/doc.pdf","source_host":"example.test","published_at":"2026-04-30"}]}',
        encoding="utf-8",
    )
    cases.write_text(
        '{"id":"case","split":"test","source_type":"annual_report","corpus_version":"sha256:test","query":"测试公司 2025 年营业收入","expected":{"answer_facts":[{"name":"revenue","value":"123","unit":"CNY"}],"relevant_evidence_ids":["doc:p1:c0"],"required_citations":[{"filename":"doc.pdf","page":1,"section":"报告"}],"abstain_allowed":false},"tags":[],"provenance":{"origin":"manual_expert_case","reviewer":"pending_human_review","reviewed_at":""}}\n',
        encoding="utf-8",
    )

    result = run(cases, chunks, source_manifest=manifest, k=1)

    assert result["label_integrity"]["valid"] is True
    assert result["results"]["bm25_entity_period_scoped"]["hit_rate_at_1"] == 1.0


def test_label_integrity_finds_fact_in_wrong_chunk_and_suggests_candidate() -> None:
    cases = [{
        "id": "case-1",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "123456.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p1:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 1, "section": "年度报告"}],
        },
    }]
    corpus = [
        {"evidence_id": "doc:p1:c0", "document_id": "doc", "filename": "doc.pdf", "page": 1, "section": "年度报告 / 概况", "content": "无目标数值"},
        {"evidence_id": "doc:p2:c0", "document_id": "doc", "filename": "doc.pdf", "page": 2, "section": "年度报告 / 财务数据", "content": "营业收入 123,456.00 元"},
    ]

    result = validate_label_integrity(cases, corpus)

    assert result["valid"] is False
    assert result["errors"][0]["missing_fact_values"] == ["123456.00"]
    assert result["errors"][0]["suggested_evidence_ids_by_fact"]["123456.00"] == ["doc:p2:c0"]


def test_label_integrity_accepts_a_canonical_value_with_raw_evidence_value() -> None:
    cases = [{
        "id": "case-1",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "1230000.00", "evidence_value": "123.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p1:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 1, "section": "年度报告"}],
        },
    }]
    corpus = [{"evidence_id": "doc:p1:c0", "document_id": "doc", "filename": "doc.pdf", "page": 1, "section": "年度报告", "content": "单位：万元\n营业收入 123.00"}]

    assert validate_label_integrity(cases, corpus)["valid"] is True


def test_dense_and_rrf_share_the_same_entity_period_scope() -> None:
    corpus = [
        {"evidence_id": "bank", "company": "平安银行", "security_code": "000001", "report_period": "FY2024", "content": "营业收入"},
        {"evidence_id": "liquor", "company": "贵州茅台", "security_code": "600519", "report_period": "FY2024", "content": "营业收入"},
    ]
    embedding = lambda texts: [[1.0, 0.0] if "收入" in text else [0.0, 1.0] for text in texts]
    retrievers = build_scoped_retriever_bundle(corpus, embedding)

    for retriever in retrievers.values():
        results = retriever("平安银行 2024 年营业收入", top_k=10)
        assert [item["evidence_id"] for item in results] == ["bank"]


def test_fact_diagnostic_accepts_alternative_answer_bearing_evidence() -> None:
    cases = [{
        "id": "case-1",
        "variant_type": "colloquial",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "123456.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["gold"],
        },
    }]
    corpus = [
        {"evidence_id": "gold", "content": "营业收入 123,456.00 元"},
        {"evidence_id": "alternative", "content": "本年营业收入为 123,456.00 元"},
    ]
    metrics = {"details": [{
        "id": "case-1",
        "result_ids": ["alternative"],
        "hit": False,
        "citation_hit": False,
        "abstain_retrieval_ok": None,
    }]}

    result = add_fact_support_diagnostics(metrics, cases, corpus, k=10)

    assert result["candidate_diagnostics"]["fact_value_hit_at_10"] == 1.0
    assert result["candidate_diagnostics"]["fact_and_metric_context_hit_at_10"] == 1.0
    assert result["query_variant_breakdown"]["colloquial"]["strict_recall_at_10"] == 0.0
    assert result["query_variant_breakdown"]["colloquial"]["fact_and_metric_context_hit_at_10"] == 1.0
