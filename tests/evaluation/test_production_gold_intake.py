from evaluation.production_gold_intake import REDACTION_VERSION, validate_gold_rows


HASH = "sha256:" + "a" * 64


def _row(category="financial_report", split="test", case_id="gold-1"):
    return {
        "id": case_id,
        "split": split,
        "category": category,
        "query": f"贵州茅台 2025 年年报的经营现金流是多少？（{case_id}）",
        "source": {
            "origin": "production_bad_case",
            "source_fingerprint": HASH,
            "corpus_version": HASH,
            "redaction_version": REDACTION_VERSION,
            "collected_at": "2026-08-15",
        },
        "review": {
            "reviewers": ["reviewer-a", "reviewer-b"],
            "reviewed_at": "2026-08-15T10:00:00+08:00",
            "approved": True,
        },
        "expected": {
            "answer_facts": [{"name": "经营活动现金流", "value": "...", "unit": "元"}],
            "relevant_evidence_ids": ["doc:annual-2025:p32"],
            "required_citations": [{"evidence_id": "doc:annual-2025:p32", "page": 32}],
            "abstain_allowed": False,
        },
    }


def test_gold_intake_accepts_reviewed_deidentified_rag_row():
    result = validate_gold_rows(
        [_row(split="train", case_id="gold-train"), _row(split="validation", case_id="gold-validation"), _row()],
        kind="rag", require_dual_review=True, required_categories={"financial_report"},
    )
    assert result["valid"] is True
    assert result["production_ready"] is True
    assert result["split_counts"] == {"test": 1, "train": 1, "validation": 1}


def test_gold_intake_requires_real_source_and_production_split():
    row = _row(split="validation")
    row["source"]["origin"] = "manual"
    result = validate_gold_rows([row], kind="rag", require_dual_review=True)
    assert result["valid"] is False
    assert any("source.origin" in error for error in result["errors"])


def test_gold_intake_requires_evidence_id_and_page():
    row = _row()
    row["expected"]["required_citations"] = [{"filename": "annual.pdf", "page": 0}]
    result = validate_gold_rows([row], kind="rag")
    assert result["valid"] is False
    assert any("citation 0" in error for error in result["errors"])


def test_gold_intake_without_dual_review_is_intake_only():
    row = _row()
    row["review"] = {"reviewer": "one", "reviewed_at": "2026-08-15T10:00:00Z", "approved": True}
    result = validate_gold_rows([row], kind="rag", require_dual_review=False)
    assert result["valid"] is True
    assert result["production_ready"] is False


def test_gold_intake_covers_intent_contract():
    row = _row(category="compound_task")
    row["expected"] = {
        "intent": "comparison",
        "slots": {"stock_codes": ["600519", "000858"]},
        "tasks": [{"task_type": "comparison"}],
        "clarification_required": False,
        "abstain_allowed": False,
    }
    rows = [row]
    for split, case_id in (("train", "intent-train"), ("validation", "intent-validation")):
        extra = _row(category="compound_task", split=split, case_id=case_id)
        extra["expected"] = row["expected"]
        rows.append(extra)
    result = validate_gold_rows(rows, kind="intent", require_dual_review=True)
    assert result["valid"] is True
