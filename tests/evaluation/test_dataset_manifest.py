from evaluation.dataset_manifest import verify_manifest


def test_committed_dataset_manifest_is_pinned_and_valid() -> None:
    report = verify_manifest()

    assert report["valid"] is True
    assert {entry["id"] for entry in report["datasets"]} == {
        "rag-contract-v1",
        "intent-routing-smoke-v1",
        "intent-compound-routing-smoke-v1",
        "intent-routing-robustness-candidate-v1",
        "workflow-governance-contract-v1",
        "public-filings-rag-candidate-v1",
        "public-filings-query-robustness-candidate-v1",
        "public-filings-query-rewrite-stress-candidate-v1",
        "financial-agent-e2e-candidate-v1",
        "financial-agent-e2e-review-queue-v1",
            "heldout-public-filings-manual-expert-candidate-v1",
        "heldout-public-filings-manual-expert-candidate-v2",
            "external-cfqa-source-mapping-candidate-v1",
        "financebench-open-source-public-gold-v1",
        }
    assert {entry["claim_policy"] for entry in report["datasets"]} == {
        "regression_only",
        "candidate_not_reportable",
        "public_external_gold_not_online_traffic",
    }
