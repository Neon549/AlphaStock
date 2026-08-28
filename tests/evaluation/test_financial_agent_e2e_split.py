import copy

import pytest

from evaluation.financial_agent_e2e_split import SPLIT_POLICY, assign_gold_splits
from tests.evaluation.test_financial_agent_e2e_production_admission import _case


def _cases(count=10):
    rows = []
    for index in range(count):
        case = copy.deepcopy(_case())
        case["id"] = f"real-{index:02d}"
        case["split"] = "review_queue"
        case["provenance"]["source_fingerprint"] = "sha256:" + f"{index:064x}"
        rows.append(case)
    return rows


def test_split_is_stable_and_independent_of_input_order():
    left = assign_gold_splits(_cases(), dataset_id="real-v1")
    right = assign_gold_splits(list(reversed(_cases())), dataset_id="real-v1")
    assert [(row["id"], row["split"]) for row in left] == [(row["id"], row["split"]) for row in right]
    assert {split: sum(row["split"] == split for row in left) for split in ("train", "validation", "test")} == {
        "train": 2, "validation": 2, "test": 6,
    }
    assert all(row["split_policy"] == SPLIT_POLICY for row in left)


def test_splitter_refuses_to_reshuffle_assigned_cases():
    rows = _cases()
    rows[0]["split"] = "test"
    with pytest.raises(ValueError, match="refusing to reshuffle"):
        assign_gold_splits(rows, dataset_id="real-v1")
