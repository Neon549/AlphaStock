from pathlib import Path

import pytest

from evaluation.apply_cfqa_review import apply_repairs


def test_apply_repairs_updates_only_the_derived_copy() -> None:
    cases = [{
        "id": "case-1",
        "expected": {
            "relevant_evidence_ids": ["doc:p2:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 2, "section": "old"}],
        },
    }]
    chunks = [{
        "evidence_id": "doc:p3:c0",
        "page": 3,
        "parent_path": ["报告", "表格"],
    }]
    manifest = {
        "dataset_tier": "visual_repaired_pending_independent_review",
        "reviewer_role": "assistant_visual_auditor",
        "human_review_required": True,
        "promotion_eligible": False,
        "repairs": [{"case_id": "case-1", "evidence_ids": ["doc:p3:c0"]}],
    }

    result = apply_repairs(cases, chunks, manifest)

    assert cases[0]["expected"]["relevant_evidence_ids"] == ["doc:p2:c0"]
    assert result[0]["expected"]["relevant_evidence_ids"] == ["doc:p3:c0"]
    assert result[0]["expected"]["required_citations"][0]["page"] == 3
    assert result[0]["review_sidecar"]["human_reviewer"] == ""


def test_apply_repairs_rejects_unknown_evidence() -> None:
    manifest = {
        "dataset_tier": "visual_repaired_pending_independent_review",
        "reviewer_role": "assistant_visual_auditor",
        "human_review_required": True,
        "promotion_eligible": False,
        "repairs": [{"case_id": "case-1", "evidence_ids": ["missing:p1:c0"]}],
    }

    with pytest.raises(ValueError, match="repair evidence does not exist"):
        apply_repairs([{"id": "case-1", "expected": {}}], [], manifest)


def test_apply_repairs_rejects_gold_manifest() -> None:
    manifest = {
        "dataset_tier": "production_gold",
        "reviewer_role": "assistant_visual_auditor",
        "human_review_required": False,
        "promotion_eligible": True,
        "repairs": [],
    }

    with pytest.raises(ValueError, match="must remain non-Gold"):
        apply_repairs([], [], manifest)
