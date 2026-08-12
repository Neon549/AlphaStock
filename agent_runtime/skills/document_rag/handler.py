"""Runtime entrypoint for the document-rag skill."""

from api.document_processing.retrieval import retrieve_document_evidence
from control_plane.observability import record_rag_event, redact_query


def run(*, session_id: str, query: str) -> dict:
    context, citations, retrieval = retrieve_document_evidence(session_id, query)
    record_rag_event("retrieval", {"query": redact_query(query), **retrieval})
    retrieved_ids = set(retrieval.get("retrieved_evidence_ids") or [])
    citation_ids = [str(citation.get("evidence_id") or "") for citation in citations]
    missing_ids = [evidence_id for evidence_id in citation_ids if evidence_id not in retrieved_ids]
    validation_status = (
        "not_applicable" if retrieval.get("status") == "abstained"
        else "passed" if citation_ids and not missing_ids
        else "failed"
    )
    record_rag_event("citation_validation", {
        "status": validation_status,
        "validation_type": "structural_retrieval_membership",
        "citation_count": len(citation_ids),
        "missing_evidence_ids": missing_ids,
        "retrieval_status": retrieval.get("status"),
    })
    return {
        "context": context,
        "citations": citations,
    }
