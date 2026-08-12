import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from evaluation.download_corpus import _download, load_sources, validate_sources_payload


def test_candidate_source_manifest_is_structurally_valid() -> None:
    sources = load_sources()

    assert sources["dataset_id"] == "a-share-public-filings-candidate-v1"
    assert len(sources["documents"]) == 10
    assert {document["sector"] for document in sources["documents"]} >= {"消费", "银行", "半导体设备", "光伏"}


def test_source_manifest_rejects_non_https_url() -> None:
    payload = {"documents": [{
        "document_id": "bad", "security_code": "000001", "company": "example",
        "report_period": "FY2025", "published_at": "2026-01-01", "title": "example",
        "source_host": "example", "source_url": "http://example.com/report.pdf",
    }]}

    with pytest.raises(ValueError, match="HTTPS"):
        validate_sources_payload(payload)


def test_download_accepts_a_relative_custom_target_directory(tmp_path: Path, monkeypatch) -> None:
    fake_response = Mock(content=b"%PDF-1.7 mock", headers={})
    fake_response.raise_for_status.return_value = None
    target = Path("runtime") / "download-corpus-test"
    monkeypatch.chdir(tmp_path)
    with patch("evaluation.download_corpus.requests.get", return_value=fake_response):
        record = _download({"document_id": "doc", "source_url": "https://example.test/doc.pdf"}, target, timeout_seconds=1)

    assert record["local_path"].endswith("runtime\\download-corpus-test\\doc.pdf")
