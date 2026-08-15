from unittest.mock import patch

from scripts.evaluate_intent_routing import evaluate_rows


def test_compound_evaluator_reports_detection_and_policy_metrics() -> None:
    rows = [
        {
            "id": "compound-1",
            "query": "compound query",
            "expected": {
                "intent": 2,
                "compound": {
                    "detected": True,
                    "classification": "parallel",
                    "execution_policy": "parallel_stage",
                    "task_intents": ["investment_analysis", "backtest"],
                },
                "tasks": [
                    {"intent": "investment_analysis"},
                    {"intent": "backtest"},
                ],
            },
        }
    ]
    parsed = {
        "intent": 2,
        "source": "rule",
        "sub_intents": [
            {"task_id": "analysis-1", "intent": "investment_analysis", "depends_on": [], "slots": {}},
            {"task_id": "backtest-1", "intent": "backtest", "depends_on": [], "slots": {}},
        ],
        "compound_intent": {
            "detected": True,
            "classification": "parallel",
            "execution_policy": "parallel_stage",
            "task_intents": ["investment_analysis", "backtest"],
        },
    }

    with patch("scripts.evaluate_intent_routing.parse_intent", return_value=parsed):
        report = evaluate_rows(rows)

    assert report["metrics"]["compound_detection_f1"] == 1.0
    assert report["metrics"]["compound_classification_exact"] == 1.0
    assert report["metrics"]["compound_execution_policy_exact"] == 1.0
    assert report["metrics"]["compound_task_intents_exact"] == 1.0


def test_evaluator_reports_bucketed_clarification_and_high_risk_metrics() -> None:
    rows = [
        {
            "id": "risk-and-clarify",
            "bucket": "high_risk",
            "query": "buy two stocks",
            "expected": {
                "intent": 4,
                "clarification_required": True,
                "high_risk_route": True,
                "required_missing_slots": ["stock_code"],
                "tasks": [{"intent": "trade_action", "requires_confirmation": True}],
            },
        }
    ]
    parsed = {
        "intent": 4,
        "source": "rule",
        "sub_intents": [{
            "task_id": "trade-action-1", "intent": "trade_action", "depends_on": [],
            "slots": {}, "missing_slots": ["stock_code"], "requires_confirmation": True,
        }],
        "compound_intent": {"detected": False, "classification": "single", "execution_policy": "single_task", "task_intents": ["trade_action"]},
    }

    with patch("scripts.evaluate_intent_routing.parse_intent", return_value=parsed):
        report = evaluate_rows(rows)

    assert report["metrics"]["clarification_f1"] == 1.0
    assert report["metrics"]["high_risk_route_f1"] == 1.0
    assert report["metrics"]["required_missing_slots_exact"] == 1.0
    assert report["bucket_metrics"]["high_risk"]["end_to_end_exact"] == 1.0
