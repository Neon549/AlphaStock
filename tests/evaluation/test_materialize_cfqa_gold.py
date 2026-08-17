import json
import sys
from pathlib import Path

from evaluation.materialize_cfqa_gold import main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_materialize_uses_two_digit_cfqa_year_for_document_selection(tmp_path: Path, monkeypatch) -> None:
    candidates = tmp_path / "candidates.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "cases.jsonl"
    _write_jsonl(
        candidates,
        [{
            "id": "case-20",
            "stock_code": "000001",
            "query": "某公司20年营业收入是多少？",
            "reference_answer": "营业收入 100",
            "answer_pdf_pages": [1],
        }],
    )
    _write_jsonl(
        chunks,
        [
            {"document_id": "doc-2019", "security_code": "000001", "report_period": "2019", "page": 1, "evidence_id": "doc-2019:p1:c0", "text": "营业收入 99"},
            {"document_id": "doc-2020", "security_code": "000001", "report_period": "2020", "page": 1, "evidence_id": "doc-2020:p1:c0", "text": "营业收入 100"},
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_cfqa_gold",
            "--candidates", str(candidates),
            "--chunks", str(chunks),
            "--corpus-version", "sha256:test",
            "--output", str(output),
        ],
    )

    assert main() == 0
    row = json.loads(output.read_text(encoding="utf-8").strip())
    assert row["expected"]["required_citations"][0]["filename"] == "doc-2020.pdf"


def test_materialize_records_missing_pages_without_aborting_batch(tmp_path: Path, monkeypatch) -> None:
    candidates = tmp_path / "candidates.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    output = tmp_path / "cases.jsonl"
    unresolved = tmp_path / "unresolved.jsonl"
    _write_jsonl(
        candidates,
        [{
            "id": "case-missing-page",
            "stock_code": "000001",
            "query": "某公司2020年营业收入是多少？",
            "reference_answer": "营业收入 100",
            "answer_pdf_pages": [99],
            "source": {"document_id": "doc-2020"},
        }],
    )
    _write_jsonl(
        chunks,
        [{"document_id": "doc-2020", "security_code": "000001", "report_period": "2020", "page": 1, "evidence_id": "doc-2020:p1:c0", "text": "营业收入 100"}],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_cfqa_gold",
            "--candidates", str(candidates),
            "--chunks", str(chunks),
            "--corpus-version", "sha256:test",
            "--output", str(output),
            "--unresolved-output", str(unresolved),
        ],
    )

    assert main() == 0
    assert output.read_text(encoding="utf-8") == ""
    pending = json.loads(unresolved.read_text(encoding="utf-8").strip())
    assert pending["status"] == "page_mapping_pending"
    assert pending["resolution_error"]["reason"] == "page_missing_from_index"
