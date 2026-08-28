import copy

from evaluation.financial_agent_e2e_gold_freeze import build_gold_freeze_manifest
from evaluation.financial_agent_e2e_review import case_sha256
from evaluation.financial_agent_e2e_split import SPLIT_POLICY
from tests.evaluation.test_financial_agent_e2e_production_admission import _case, _run


def _package():
    cases, reviews, runs = [], [], []
    for index, split in enumerate(("train", "validation", "test")):
        case = copy.deepcopy(_case())
        case["id"] = f"real-{index}"
        case["split"] = split
        case["split_policy"] = SPLIT_POLICY
        cases.append(case)
        for reviewer in ("reviewer-a", "reviewer-b"):
            reviews.append({
                "case_id": case["id"], "case_sha256": case_sha256(case),
                "reviewer_id": reviewer, "reviewed_at": "2026-08-28T10:00:00+10:00",
                "approved": True,
                "rubric_decisions": [
                    {"id": rubric["id"], "approved": True} for rubric in case["rubrics"]
                ],
                "allowed_evidence": ["document:1:p8"], "failure_taxonomy": [],
            })
        for run_index in range(4):
            run = copy.deepcopy(_run(run_index))
            run["case_id"] = case["id"]
            run["run_id"] = f"run-{index}-{run_index}"
            runs.append(run)
    return cases, reviews, runs


def test_freeze_produces_hashes_only_after_full_admission():
    cases, reviews, runs = _package()

    report = build_gold_freeze_manifest(
        cases, reviews, runs, dataset_id="financial-agent-real-v1",
        frozen_at="2026-08-28T12:00:00+10:00",
        train_separation="Test cases were hidden from prompt and retriever tuning.",
        minimum_cases=3,
    )

    assert report["valid"] is True
    assert report["dataset_tier"] == "production"
    assert report["split_counts"] == {"test": 1, "train": 1, "validation": 1}
    assert all(value.startswith("sha256:") for value in report["artifacts"].values())


def test_freeze_fails_closed_when_a_split_is_missing():
    cases, reviews, runs = _package()
    cases[2]["split"] = "validation"
    # Changing a case after review also invalidates its bound review hash.

    try:
        build_gold_freeze_manifest(
            cases, reviews, runs, dataset_id="financial-agent-real-v1",
            frozen_at="2026-08-28T12:00:00+10:00", train_separation="isolated",
            minimum_cases=3,
        )
    except ValueError as exc:
        assert "case_sha256" in str(exc)
    else:
        raise AssertionError("mutated reviewed case must fail admission")
