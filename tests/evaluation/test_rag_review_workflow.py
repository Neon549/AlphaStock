from evaluation.rag_review_workflow import build_review_template, promote_reviewed_cases


def _candidate() -> dict:
    return {
        "id": "case-1",
        "split": "test",
        "source_type": "annual_report",
        "corpus_version": "sha256:abc",
        "query": "2025 年营业收入是多少？",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "20.50", "unit": "billion CNY"}],
            "relevant_evidence_ids": ["doc:p18:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 18, "section": "财务报表"}],
            "abstain_allowed": False,
        },
        "provenance": {"origin": "candidate", "reviewer": "pending_human_review", "reviewed_at": ""},
    }


def test_review_template_requires_explicit_human_approval() -> None:
    template = build_review_template([_candidate()])
    promoted, errors = promote_reviewed_cases([_candidate()], template)

    assert promoted == []
    assert errors == ["case-1: review decision must be approved"]


def test_promotion_rewrites_candidate_as_reviewed_validation_case() -> None:
    review = build_review_template([_candidate()])[0]
    review.update({"decision": "approved", "reviewer": "reviewer-a", "reviewed_at": "2026-08-12"})

    promoted, errors = promote_reviewed_cases([_candidate()], [review])

    assert errors == []
    assert promoted[0]["split"] == "validation"
    assert promoted[0]["provenance"]["origin"] == "human_reviewed_public_filing_candidate"


def test_promotion_rejects_partial_review_set() -> None:
    promoted, errors = promote_reviewed_cases([_candidate()], [])

    assert promoted == []
    assert errors == ["case-1: missing review"]
