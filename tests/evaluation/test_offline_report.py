from evaluation.offline_report import build_report


def test_offline_report_keeps_fixture_scores_inside_regression_boundary() -> None:
    report = build_report()

    assert report["passed"] is True
    assert report["manifest"]["valid"] is True
    assert report["rag"]["retrieval"]["metrics"]["cases"] == 3
    assert report["workflow_governance"] == {"cases": 8, "passed": True, "failures": []}
    assert report["routing"]["executed"] is False
    assert "not production quality" in report["claim_boundary"]
