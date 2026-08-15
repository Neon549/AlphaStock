from evaluation.run_query_rewrite_ab_eval import _case_deltas


def test_case_delta_counts_hit_and_rank_improvements() -> None:
    baseline = {"details": [
        {"id": "win", "hit": False, "rank": None, "result_ids": []},
        {"id": "loss", "hit": True, "rank": 1, "result_ids": ["a"]},
        {"id": "tie", "hit": True, "rank": 2, "result_ids": ["b"]},
    ]}
    rewritten = {"details": [
        {"id": "win", "hit": True, "rank": 3, "result_ids": ["a"]},
        {"id": "loss", "hit": False, "rank": None, "result_ids": []},
        {"id": "tie", "hit": True, "rank": 2, "result_ids": ["b"]},
    ]}

    result = _case_deltas(baseline, rewritten)

    assert (result["wins"], result["losses"], result["ties"]) == (1, 1, 1)
