from evaluation.run_rag_e2e_eval import (
    deterministic_judge,
    evaluate_rows,
    parse_generated_answer,
    _generate_evidence_pack,
    _calculation_supported_by_retrieved,
)


def test_parse_structured_answer_from_fenced_json() -> None:
    parsed = parse_generated_answer(
        '```json\n{"answer":"12.3","citations":[{"filename":"x.pdf","page":2}],"abstained":false}\n```'
    )
    assert parsed["parse_ok"] is True
    assert parsed["answer"] == "12.3"
    assert parsed["citations"] == [{"filename": "x.pdf", "page": 2}]


def test_deterministic_judge_matches_numeric_fact() -> None:
    case = {"expected": {"answer_facts": [{"name": "revenue", "value": "123000", "unit": "CNY"}]}}
    assert deterministic_judge(case, "Revenue was 123,000 CNY")["correct"] is True
    assert deterministic_judge(case, "Revenue was 120,000 CNY")["correct"] is False


def test_deterministic_judge_accepts_table_ranges_but_rejects_wrong_unit() -> None:
    case = {"expected": {"answer_facts": [{"name": "price", "value": "49.5", "unit": "元"}]}}
    assert deterministic_judge(case, "价格区间为45.00-49.5元")["correct"] is True
    assert deterministic_judge(case, "价格区间为45.00-49.5万元")["correct"] is False
    scaled = {"expected": {"answer_facts": [{"name": "funding", "value": "1700000000", "unit": "元"}]}}
    assert deterministic_judge(scaled, "募集资金17亿元")["correct"] is True


def test_calculation_diagnostic_checks_retrieved_operands() -> None:
    case = {
        "expected": {
            "calculation": {
                "name": "ratio",
                "formula": "7458539/8136757*100",
                "expected_value": "91.66",
                "numerator": {"value": "7458539"},
                "denominator": {"value": "8136757"},
            }
        }
    }
    assert _calculation_supported_by_retrieved(case, [{"content": "资产 8,136,757；负债 7,458,539"}]) is True
    assert _calculation_supported_by_retrieved(case, [{"content": "资产 8,136,757"}]) is False


def test_evidence_pack_is_a_diagnostic_with_all_retrieved_pages() -> None:
    parsed = parse_generated_answer(_generate_evidence_pack("revenue", [{
        "filename": "doc.pdf",
        "page": 2,
        "content": "Revenue 123",
    }, {
        "filename": "doc.pdf",
        "page": 3,
        "content": "Revenue growth 10%",
    }]))
    assert parsed["answer"] == "[doc.pdf p.2]\nRevenue 123\n\n[doc.pdf p.3]\nRevenue growth 10%"
    assert parsed["citations"] == [{"filename": "doc.pdf", "page": 2}, {"filename": "doc.pdf", "page": 3}]
    assert parsed["abstained"] is False


def test_e2e_report_separates_answer_and_grounded_accuracy() -> None:
    cases = [{
        "id": "case-1",
        "query": "What was revenue?",
        "corpus_version": "sha256:test",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "123", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p2:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 2, "section": ""}],
        },
    }]
    retrieved = [{
        "evidence_id": "doc:p2:c0:c0",
        "page_evidence_id": "doc:p2:c0",
        "filename": "doc.pdf",
        "page": 2,
        "content": "Revenue 123",
    }]

    def retriever(query: str, *, top_k: int):
        return retrieved[:top_k]

    def generator(query: str, contexts: list[dict]):
        return '{"answer":"123 CNY","citations":[{"filename":"doc.pdf","page":2}],"abstained":false}'

    report = evaluate_rows(
        cases,
        retriever,
        k=10,
        generator=generator,
        judge=deterministic_judge,
    )
    assert report["answer_accuracy"] == 1.0
    assert report["grounded_answer_accuracy"] == 1.0
    assert report["retrieval_hit_rate_at_k"] == 1.0
