from evaluation.resolve_cfqa_sources import choose_full_report


def test_choose_full_report_prefers_original_over_revision_and_summary() -> None:
    result = choose_full_report(
        [
            {
                "announcementTitle": "某公司2020年年度报告摘要",
                "announcementTime": 1600000000000,
                "adjunctUrl": "finalpage/2021-01-01/summary.PDF",
            },
            {
                "announcementTitle": "某公司2020年年度报告（修订版）",
                "announcementTime": 1610000000000,
                "adjunctUrl": "finalpage/2021-01-02/revised.PDF",
            },
            {
                "announcementTitle": "某公司2020年年度报告",
                "announcementTime": 1609000000000,
                "adjunctUrl": "finalpage/2021-01-01/full.PDF",
            },
        ],
        2020,
    )

    assert result is not None
    assert result["source_url"].endswith("/full.PDF")
    assert result["title"] == "某公司2020年年度报告"


def test_choose_full_report_strips_cninfo_highlight_markup() -> None:
    result = choose_full_report(
        [
            {
                "announcementTitle": "某公司<em>2021年</em><em>年度报告</em>",
                "announcementTime": 1640000000000,
                "adjunctUrl": "finalpage/2022-01-01/full.PDF",
            }
        ],
        2021,
    )

    assert result is not None
    assert result["title"] == "某公司2021年年度报告"
