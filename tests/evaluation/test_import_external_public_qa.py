from __future__ import annotations

from pathlib import Path

from evaluation.import_external_public_qa import normalize_cfqa, normalize_fintruthqa


def test_cfqa_normalization_preserves_page_order_and_provenance(tmp_path: Path) -> None:
    source = tmp_path / "cfqa.json"
    source.write_text("[]", encoding="utf-8")
    rows = normalize_cfqa(
        [
            {
                "股票代码": "600519",
                "公司": "贵州茅台",
                "问题": "2025年营业收入是多少？",
                "答案": "营业收入为100元。",
                "答案出自": [[12, 13], [13]],
                "id": 7,
            }
        ],
        split="test",
        dataset_path=source,
        repository_commit="abc123",
    )
    assert rows[0]["answer_pdf_pages"] == [12, 13]
    assert rows[0]["evidence_status"] == "pdf_mapping_pending"
    assert rows[0]["provenance"]["origin"] == "public_real_investor_qa"
    assert rows[0]["provenance"]["repository_commit"] == "abc123"


def test_fintruthqa_normalization_keeps_quality_labels_without_fake_evidence(tmp_path: Path) -> None:
    source = tmp_path / "fintruthqa.csv"
    source.write_text("", encoding="utf-8")
    rows = normalize_fintruthqa(
        [
            {
                "QUES": "股东人数是多少？",
                "ANS": "公司回复：1000人。",
                "IS_QUESTION": "Positive",
                "QUES_RELEVANCE": "Positive",
                "ANS_READABILITY": "4",
                "ANS_RELEVANCE": "3",
            }
        ],
        dataset_path=source,
        repository_commit="def456",
    )
    assert rows[0]["quality_labels"]["answer_readability"] == "4"
    assert rows[0]["evidence_status"] == "no_page_level_evidence"
    assert rows[0]["provenance"]["license"] == "Apache-2.0"
