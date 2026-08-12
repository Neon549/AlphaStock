from evaluation.frozen_dataset import load_jsonl
from evaluation.user_query_intake import DEFAULT_DATASET, validate_intake_rows


def test_user_query_intake_is_valid_and_keeps_non_rag_lanes() -> None:
    report = validate_intake_rows(load_jsonl(DEFAULT_DATASET))

    assert report["valid"] is True
    assert report["case_count"] == 13
    assert report["lane_counts"]["clarification"] == 3
    assert report["lane_counts"]["agent_governance"] == 4
    assert report["high_risk_cases"] == 6


def test_user_query_intake_rejects_identity_fields_and_pii() -> None:
    report = validate_intake_rows([{
        "id": "bad", "query": "请回复 x@example.com", "session_id": "secret",
        "provenance": {"origin": "manual_expert_case"},
        "evaluation_lane": "clarification", "runtime_route": "clarify", "risk_level": "low",
        "requires_fresh_source": False, "requires_human_review": False, "missing_slots": [],
    }])

    assert report["valid"] is False
    assert "forbidden identity field" in " ".join(report["errors"])
    assert "possible email" in " ".join(report["errors"])
