from evaluation.frozen_dataset import validate_intent_rows, validate_rag_rows


def test_rag_validator_accepts_human_reviewed_pinned_case() -> None:
    report = validate_rag_rows([
        {
            "id": "rag-1",
            "split": "test",
            "source_type": "annual_report",
            "corpus_version": "sha256:abc",
            "query": "What was revenue?",
                "expected": {
                    "answer_facts": [{"name": "revenue", "value": "20.50", "unit": "billion CNY"}],
                    "relevant_evidence_ids": ["doc:p18:0"],
                "required_citations": [{"page": 18}],
                "abstain_allowed": False,
            },
            "provenance": {"origin": "manual", "reviewer": "reviewer-a", "reviewed_at": "2026-08-11"},
        }
    ])

    assert report["valid"] is True


def test_intent_validator_rejects_unconfirmed_trade_action() -> None:
    report = validate_intent_rows([
        {
            "id": "intent-1",
            "query": "Buy 600519 for me",
            "expected": {"intent": 4, "analyst_focus": None, "tasks": [{"intent": "trade_action"}]},
            "provenance": {"origin": "manual", "reviewer": "reviewer-a", "reviewed_at": "2026-08-11"},
        }
    ])

    assert report["valid"] is False
    assert "requires explicit confirmation" in " ".join(report["errors"])


def test_intent_validator_rejects_an_invalid_compound_contract() -> None:
    report = validate_intent_rows([
        {
            "id": "intent-compound-1",
            "query": "Analyze then backtest 600519",
            "expected": {
                "intent": 2,
                "analyst_focus": "all",
                "compound": {
                    "detected": True,
                    "classification": "unknown",
                    "execution_policy": "single_task",
                    "task_intents": ["investment_analysis", "backtest"],
                },
                "tasks": [
                    {"intent": "investment_analysis"},
                    {"intent": "backtest", "depends_on_intents": ["investment_analysis"]},
                ],
            },
            "provenance": {"origin": "manual", "reviewer": "reviewer-a", "reviewed_at": "2026-08-13"},
        }
    ])

    assert report["valid"] is False
    assert "invalid expected.compound contract" in " ".join(report["errors"])
