from control_plane.evidence_status import build_evidence_status


def test_distinguishes_not_requested_from_no_hit_and_retrieval_error():
    not_requested = build_evidence_status()
    assert not_requested["document_rag"]["status"] == "not_requested"

    no_hit = build_evidence_status(
        rag_events=[{
            "event": "retrieval", "status": "abstained",
            "abstain_reason": "no_retrieval_hits", "retrieved_chunk_count": 0,
        }],
    )
    assert no_hit["document_rag"]["status"] == "no_hit"

    error = build_evidence_status(
        rag_events=[{
            "event": "retrieval", "status": "abstained",
            "abstain_reason": "retrieval_error", "retrieved_chunk_count": 0,
        }],
    )
    assert error["document_rag"]["status"] == "error"


def test_distinguishes_market_source_failure_from_stale_evidence():
    failed = build_evidence_status(observations=[{
        "tool": "market-price", "source_kind": "market_evidence", "ok": False,
    }])
    assert failed["market_data"]["status"] == "error"

    stale = build_evidence_status(observations=[{
        "tool": "financial-indicators", "source_kind": "market_evidence", "ok": True,
        "freshness": {"status": "stale"},
    }])
    assert stale["market_data"]["status"] == "stale_rejected"


def test_output_gate_status_is_reported_without_changing_policy():
    result = build_evidence_status(
        observations=[{
            "tool": "market-price", "source_kind": "market_evidence", "ok": True,
            "freshness": {"status": "retrieved"},
        }],
        publish_status="blocked",
        publish_reasons=["evidence gap"],
    )
    assert result["market_data"]["status"] == "success"
    assert result["output_gate"]["status"] == "blocked"
    assert result["output_gate"]["publish_reasons"] == ["evidence gap"]
