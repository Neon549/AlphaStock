from evaluation.auto_review_rag_candidates import audit_cases
from evaluation.run_candidate_rag_eval import FACT_CONTEXT_ALIASES


def _ablation(case_id: str, *, abstain_ok: bool | None = None) -> dict:
    return {
        "results": {
            "bm25_entity_period_scoped": {
                "details": [{
                    "id": case_id,
                    "result_ids": [],
                    "abstain_retrieval_ok": abstain_ok,
                }]
            }
        }
    }


def test_auto_audit_accepts_exact_current_fact_and_citation() -> None:
    cases = [{
        "id": "case-exact",
        "query": "2025 revenue",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "123456.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p1:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 1, "section": "Revenue"}],
            "abstain_allowed": False,
        },
    }]
    corpus = [{
        "evidence_id": "doc:p1:c0",
        "document_id": "doc",
        "filename": "doc.pdf",
        "page": 1,
        "section": "Revenue",
        "content": f"{FACT_CONTEXT_ALIASES['revenue'][0]} 123,456.00",
    }]

    report = audit_cases(cases, corpus, ablation=_ablation("case-exact"))

    assert report["summary"]["by_status"] == {"auto_accept_current": 1}
    assert report["items"][0]["confidence"] == "high"


def test_auto_audit_suggests_page_repair_without_changing_label() -> None:
    cases = [{
        "id": "case-repair",
        "query": "2025 revenue",
        "expected": {
            "answer_facts": [{"name": "revenue", "value": "123456.00", "unit": "CNY"}],
            "relevant_evidence_ids": ["doc:p1:c0"],
            "required_citations": [{"filename": "doc.pdf", "page": 1, "section": "Revenue"}],
            "abstain_allowed": False,
        },
    }]
    corpus = [
        {"evidence_id": "doc:p1:c0", "document_id": "doc", "filename": "doc.pdf", "page": 1, "section": "Revenue", "content": "Revenue not disclosed"},
        {"evidence_id": "doc:p2:c0", "document_id": "doc", "filename": "doc.pdf", "page": 2, "section": "Revenue", "content": f"{FACT_CONTEXT_ALIASES['revenue'][0]} 123,456.00"},
    ]

    report = audit_cases(cases, corpus, ablation=_ablation("case-repair"))

    item = report["items"][0]
    assert item["status"] == "auto_repair_candidate"
    assert item["recommended_evidence_ids"] == ["doc:p2:c0"]
    assert item["current_label_checks"]["current_evidence_ids"] == ["doc:p1:c0"]


def test_auto_audit_accepts_retrieval_compliant_abstention() -> None:
    cases = [{
        "id": "case-abstain",
        "query": "2026 Q1 revenue",
        "expected": {
            "answer_facts": [],
            "relevant_evidence_ids": [],
            "required_citations": [],
            "abstain_allowed": True,
        },
    }]

    report = audit_cases(cases, [], ablation=_ablation("case-abstain", abstain_ok=True))

    assert report["summary"]["by_status"] == {"auto_accept_abstention": 1}
