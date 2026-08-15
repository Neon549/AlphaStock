from control_plane.evidence_status import build_evidence_status
from control_plane.execution_status import build_execution_status


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


def test_execution_status_distinguishes_input_http_and_mcp_channels():
    result = build_execution_status(
        input_received=True,
        input_length=12,
        input_sha256="a" * 64,
        run_metadata={"trigger": "mcp", "channel": "mcp", "operation": "mcp_research_stock"},
        external_calls=[
            {"target": "market-price", "ok": True, "latency_ms": 10},
            {"target": "stock-news", "ok": False, "latency_ms": 20},
        ],
    )
    assert result["user_input"]["status"] == "received"
    assert result["external_http"]["status"] == "partial"
    assert result["mcp_tools"]["status"] == "success"


def test_execution_status_reports_no_calls_separately_from_failures():
    result = build_execution_status(input_received=False)
    assert result["user_input"]["status"] == "empty"
    assert result["external_http"]["status"] == "not_requested"
    assert result["mcp_tools"]["status"] == "not_requested"
