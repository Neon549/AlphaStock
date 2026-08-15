import pytest

from agent_runtime.governance.approval_modes import (
    ASSIST,
    FULL_ACCESS,
    SAFE,
    ApprovalModeConfirmationRequired,
    classify_memory_candidate,
    route_memory_candidate,
    set_approval_mode,
)


def test_three_layer_funnel_routes_by_mode_and_risk():
    assert classify_memory_candidate(
        category="operations", title="Fallback", content="Use a bounded fallback after timeout."
    ) == "low"
    assert classify_memory_candidate(
        category="research", title="Evidence", content="Separate source conflict from claim validation."
    ) == "medium"
    assert classify_memory_candidate(
        category="workflow", title="Trade instruction", content="Never issue a buy instruction."
    ) == "high"

    assert route_memory_candidate(SAFE, "low") == "manual_review"
    assert route_memory_candidate(ASSIST, "low") == "auto_approve"
    assert route_memory_candidate(ASSIST, "medium") == "batch_confirmation"
    assert route_memory_candidate(FULL_ACCESS, "medium") == "auto_approve"
    assert route_memory_candidate(FULL_ACCESS, "high") == "batch_confirmation"


def test_full_access_requires_explicit_confirmation():
    with pytest.raises(ApprovalModeConfirmationRequired):
        set_approval_mode("alice", FULL_ACCESS, confirm_risk=False)
