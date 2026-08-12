from evaluation.run_rag_e2e_eval import (
    deterministic_judge,
    evaluate_rows,
    parse_generated_answer,
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
