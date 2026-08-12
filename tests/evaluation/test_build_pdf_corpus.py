import pytest

from evaluation.build_pdf_corpus import chunk_text, section_heading


def test_chunk_text_preserves_overlap_and_rejects_invalid_configuration() -> None:
    chunks = chunk_text("第一句。第二句。第三句。第四句。", chunk_size=8, overlap=2)

    assert len(chunks) > 1
    assert chunks[0][-2:] in chunks[1]
    with pytest.raises(ValueError):
        chunk_text("x", chunk_size=10, overlap=10)


def test_section_heading_recognises_chinese_report_heading() -> None:
    assert section_heading("第十节 财务报告\n这里是内容") == "第十节 财务报告"
