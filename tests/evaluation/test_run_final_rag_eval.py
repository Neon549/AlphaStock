import json
from pathlib import Path

import pytest

from evaluation.run_final_rag_eval import run_final_evaluation


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _case() -> dict:
    return {
        "id": "real-1", "split": "test", "source_type": "quarterly_report", "corpus_version": "sha256:new-corpus",
        "query": "新公司 2026 年一季度营业收入是多少？",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "100.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["000777-2026-q1:p1:c0"],
            "required_citations": [{"filename": "000777-2026-q1.pdf", "page": 1, "section": "主要会计数据"}],
            "abstain_allowed": False,
        },
        "provenance": {"origin": "deidentified_session", "reviewer": "reviewer-b", "reviewed_at": "2026-08-12"},
    }


def test_final_runner_requires_admission_and_reports_confidence_intervals(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    references = tmp_path / "reference.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    sources = tmp_path / "sources.json"
    _write_jsonl(cases, [_case()])
    _write_jsonl(references, [{
        **_case(),
        "id": "historical-validation-1",
        "query": "旧公司 2024 年营业收入是多少？",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "1.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["old:p1:c0"],
            "required_citations": [{"filename": "old-2024.pdf", "page": 1, "section": "财务数据"}],
            "abstain_allowed": False,
        },
    }])
    _write_jsonl(chunks, [{
        "evidence_id": "000777-2026-q1:p1:c0", "document_id": "000777-2026-q1", "page": 1,
        "parent_path": ["主要会计数据"], "text": "新公司 2026 年第一季度营业收入 100.00 CNY",
    }])
    sources.write_text(json.dumps({"documents": [{
        "document_id": "000777-2026-q1", "company": "新公司", "security_code": "000777", "report_period": "2026Q1",
        "title": "新公司 2026 年第一季度报告", "source_url": "https://example.test/000777-2026-q1.pdf",
        "source_host": "example.test", "published_at": "2026-04-30",
    }]}, ensure_ascii=False), encoding="utf-8")

    report = run_final_evaluation(cases, chunks, sources, [references], k=1)

    assert report["dataset_tier"] == "production_final_test"
    assert report["results"]["bm25_entity_period_scoped"]["recall_at_1"] == 1.0
    assert report["results"]["bm25_entity_period_scoped"]["uncertainty"]["recall_at_1"]["cases"] == 1


def test_final_runner_refuses_unadmitted_candidate(tmp_path: Path) -> None:
    cases = tmp_path / "cases.jsonl"
    _write_jsonl(cases, [{**_case(), "provenance": {"origin": "manual", "reviewer": "", "reviewed_at": ""}}])

    with pytest.raises(ValueError, match="admission failed"):
        run_final_evaluation(cases, tmp_path / "missing.jsonl", tmp_path / "sources.json", [])
