from evaluation.corpus_preflight import classify_page_text, load_lock


def test_corpus_lock_describes_the_downloaded_candidate_batch() -> None:
    lock = load_lock()

    assert lock["document_count"] == 10
    assert lock["corpus_snapshot"].startswith("sha256:")
    assert len(lock["documents"]) == 10


def test_page_classifier_marks_empty_extraction_for_ocr_review() -> None:
    assert classify_page_text("   \n")["low_text"] is True
    assert classify_page_text("现金流量表 单位：元 " + "数字" * 100)["table_or_financial_marker"] is True
