"""Runtime entrypoint for the document-rag skill."""

from api.document_processing.retrieval import retrieve_document_evidence
from control_plane.observability import record_rag_event, redact_query


def run(*, session_id: str, query: str) -> dict:
    context, citations, retrieval = retrieve_document_evidence(session_id, query)
    record_rag_event("retrieval", {"query": redact_query(query), **retrieval})
    citation_ids = [str(item.get("evidence_id") or "") for item in citations]
    missing = [item for item in citation_ids if item not in set(retrieval.get("retrieved_evidence_ids") or [])]
    record_rag_event("citation_validation", {"status": "not_applicable" if retrieval["status"] == "abstained" else "passed" if citation_ids and not missing else "failed", "citation_count": len(citation_ids), "missing_evidence_ids": missing})
    return {
        "context": context,
        "citations": citations,
    }
