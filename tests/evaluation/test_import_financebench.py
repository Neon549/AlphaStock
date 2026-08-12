from evaluation.import_financebench import _report_period, build_cases
from evaluation.run_financebench_eval import _aggregate_details


def test_financebench_report_period_preserves_quarter() -> None:
    assert _report_period("APPLE_2023Q3_10Q", 2023) == "2023Q3"
    assert _report_period("3M_2018_10K", 2018) == "FY2018"


def test_financebench_cases_preserve_public_gold_pages_and_answer() -> None:
    rows = [{
        "financebench_id": "financebench_id_1",
        "doc_name": "TEST_2022_10K",
        "question": "What was revenue?",
        "answer": "$100",
        "justification": "The revenue line is 100.",
        "question_type": "metrics-generated",
        "question_reasoning": "Information extraction",
        "evidence": [{
            "doc_name": "TEST_2022_10K",
            "evidence_page_num": 4,
            "evidence_text": "Revenue 100",
        }],
    }]

    cases = build_cases(rows, corpus_snapshot="sha256:abc", repository_commit="commit-1")

    assert cases[0]["expected"]["relevant_evidence_ids"] == ["TEST_2022_10K:p5:c0"]
    assert cases[0]["expected"]["required_citations"] == [{"filename": "TEST_2022_10K.pdf", "page": 5, "section": ""}]
    assert cases[0]["reference_answer"] == "$100"
    assert cases[0]["gold_annotation"]["doc_name"] == "TEST_2022_10K"
    assert cases[0]["provenance"]["origin"] == "public_human_annotated_financebench"


def test_gold_document_aggregation_keeps_retrieval_metrics_explicit() -> None:
    report = _aggregate_details([{
        "id": "case-1", "hit": True, "recall": 1.0, "precision": 0.1,
        "rank": 2, "ndcg": 0.6309, "citation_hit": True,
        "abstain_retrieval_ok": None, "result_ids": ["doc:p2:c0"],
    }], [], k=10)

    assert report["recall_at_10"] == 1.0
    assert report["mrr"] == 0.5
    assert report["evaluation_note"].startswith("Gold document scope")
