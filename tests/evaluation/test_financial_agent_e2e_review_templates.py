from evaluation.financial_agent_e2e_review import case_sha256
from evaluation.financial_agent_e2e_review_templates import build_review_template
from tests.evaluation.test_financial_agent_e2e_production_admission import _case


def test_template_is_blank_and_bound_to_exact_case():
    case = _case()

    rows = build_review_template([case], "reviewer-a")

    assert rows[0]["case_sha256"] == case_sha256(case)
    assert rows[0]["approved"] is None
    assert rows[0]["reviewed_at"] is None
    assert all(item["approved"] is None for item in rows[0]["rubric_decisions"])
