import unittest

from evaluation.financial_agent_e2e_review import build_review_report, case_sha256, validate_reviews


def _case(origin="deidentified_session"):
    return {
        "id": "case-1",
        "provenance": {
            "origin": origin,
            "source_fingerprint": "sha256:" + "a" * 64,
            "redaction_version": "financial-agent-e2e-redaction/v1",
        },
    }


def _review(reviewer_id, *, approved=True, evidence=None, case=None):
    case = case or _case()
    return {
        "case_id": "case-1", "case_sha256": case_sha256(case),
        "reviewer_id": reviewer_id, "reviewed_at": "2026-08-15",
        "approved": approved, "rubric_decisions": [{"id": "entity", "approved": True}],
        "allowed_evidence": evidence or ["document:1:p8"], "failure_taxonomy": [],
    }


class FinancialAgentE2EReviewTests(unittest.TestCase):
    def test_matching_independent_reviews_of_real_source_are_admissible(self):
        report = build_review_report([_case()], [_review("a"), _review("b")])
        self.assertEqual(report["status_counts"], {"ready_for_production_admission": 1})

    def test_synthetic_source_cannot_be_promoted(self):
        case = _case("synthetic_candidate")
        report = build_review_report([case], [_review("a", case=case), _review("b", case=case)])
        self.assertEqual(report["status_counts"], {"consensus_approved_not_admissible_source": 1})

    def test_disagreement_requires_arbitration(self):
        report = build_review_report([_case()], [_review("a"), _review("b", evidence=["document:1:p9"])])
        self.assertEqual(report["status_counts"], {"needs_arbitration": 1})

    def test_arbitrator_can_resolve_a_disagreement_for_an_admissible_source(self):
        arbitrator = _review("c")
        arbitrator["role"] = "arbitrator"
        arbitrator["resolution"] = {
            "approved": True, "rubric_decisions": [{"id": "entity", "approved": True}],
            "allowed_evidence": ["document:1:p8"], "failure_taxonomy": [],
        }
        report = build_review_report([_case()], [_review("a"), _review("b", evidence=["document:1:p9"]), arbitrator])
        self.assertEqual(report["status_counts"], {"ready_for_production_admission": 1})

    def test_duplicate_reviewer_is_rejected(self):
        report = validate_reviews([_case()], [_review("a"), _review("a")])
        self.assertFalse(report["valid"])
        self.assertIn("duplicate review", report["errors"][0])

    def test_review_cannot_be_reused_after_case_changes(self):
        case = _case()
        reviews = [_review("a", case=case), _review("b", case=case)]
        case["query"] = "changed after review"

        report = validate_reviews([case], reviews)

        self.assertFalse(report["valid"])
        self.assertTrue(any("case_sha256" in error for error in report["errors"]))

    def test_arbitrator_must_be_independent_from_primary_reviewers(self):
        arbitrator = _review("a")
        arbitrator["role"] = "arbitrator"
        arbitrator["resolution"] = {
            "approved": True, "rubric_decisions": [{"id": "entity", "approved": True}],
            "allowed_evidence": ["document:1:p8"], "failure_taxonomy": [],
        }
        report = validate_reviews(
            [_case()], [_review("a"), _review("b", evidence=["document:1:p9"]), arbitrator]
        )
        self.assertFalse(report["valid"])
        self.assertTrue(any("duplicate review" in error for error in report["errors"]))

    def test_third_primary_review_cannot_change_the_selected_consensus_pair(self):
        report = validate_reviews([_case()], [_review("a"), _review("b"), _review("c")])
        self.assertFalse(report["valid"])
        self.assertTrue(any("two primary review slots" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
