import hashlib
import tempfile
from pathlib import Path

from evaluation.build_rag_query_variants import build_variants, write_dataset


def test_builds_four_distinct_robustness_variants_per_case() -> None:
    cases = [{
        "id": "revenue",
        "query": "贵州茅台 2024 年营业收入是多少？",
        "corpus_version": "v1",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "1", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p1:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 1}],
            "abstain_allowed": False,
        },
        "tags": [],
    }]
    sources = [{"document_id": "doc", "security_code": "600519", "company": "贵州茅台", "report_period": "FY2024"}]

    rows = build_variants(cases, sources)

    assert len(rows) == 4
    assert {row["variant_type"] for row in rows} == {"stock_code_short", "colloquial", "filing_anchored", "mixed_identifier"}
    assert next(row for row in rows if row["variant_type"] == "stock_code_short")["query"] == "600519 2024 营业收入"
    assert all(row["provenance"]["reviewed_at"] == "" for row in rows)


def test_quarterly_abstention_variants_keep_q1_marker() -> None:
    cases = [{
        "id": "missing-q1",
        "query": "宁德时代 2026 年第一季度营业收入是多少？",
        "corpus_version": "v1",
        "expected": {"answer_facts": [], "relevant_evidence_ids": [], "required_citations": [], "abstain_allowed": True},
        "tags": ["abstention"],
    }]
    sources = [{"document_id": "catl", "security_code": "300750", "company": "宁德时代", "report_period": "FY2025"}]

    rows = build_variants(cases, sources)

    assert all(any(marker in row["query"] for marker in ("Q1", "一季度", "第一季度")) for row in rows)


def test_snapshot_hash_pins_exact_written_bytes() -> None:
    runtime_dir = Path(__file__).resolve().parents[2] / "runtime"
    with tempfile.TemporaryDirectory(dir=runtime_dir) as temporary_dir:
        out = Path(temporary_dir) / "variants.jsonl"
        snapshot_out = Path(temporary_dir) / "snapshot.json"

        snapshot = write_dataset([{"id": "中文-case"}], out, snapshot_out)

        assert snapshot["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
