from evaluation.build_financial_agent_e2e_review_queue import build_rows
from evaluation.financial_agent_e2e import validate_cases


def test_builds_frozen_96_case_review_queue_with_required_coverage() -> None:
    rows = build_rows()

    assert len(rows) == 96
    assert validate_cases(rows)["valid"] is True
    categories = {row["category"] for row in rows}
    assert {
        "single_stock_fact", "multi_source_research", "cross_report_period",
        "context_reference", "missing_information_clarification", "compound_task",
        "high_risk_trade", "high_risk_publication", "upstream_recovery",
    } <= categories
    assert sum(row["risk_level"] == "high" for row in rows) >= 4
